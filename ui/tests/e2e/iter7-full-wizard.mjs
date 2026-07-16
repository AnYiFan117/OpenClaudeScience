// Walk wizard to completion — observe transition to normal assistant
import { chromium } from "playwright";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = "http://127.0.0.1:3000";
const RUNTIME = "http://127.0.0.1:2024";
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter7-full-wizard-${stamp}`);
mkdirSync(runDir, { recursive: true });

const wsPath = `/tmp/e2e_iter7_${Date.now()}`;
mkdirSync(wsPath, { recursive: true });

const events = [];
function log(m) { const l = `[${new Date().toISOString()}] ${m}`; console.log(l); appendFileSync(join(runDir, "run.log"), l + "\n"); }
function evt(t, d) { events.push({ t: new Date().toISOString(), type: t, ...d }); }

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
page.on("dialog", async (d) => { if (d.type() === "prompt") await d.accept(wsPath); else await d.accept(); });
page.on("console", (m) => evt("console", { level: m.type(), text: m.text().slice(0, 400) }));
page.on("pageerror", (e) => evt("pageerror", { message: e.message }));
page.on("response", (r) => { if (r.status() >= 400) evt("badResp", { url: r.url(), status: r.status() }); });

let shotIdx = 0;
async function shot(name) {
  shotIdx += 1;
  await page.screenshot({ path: join(runDir, `${String(shotIdx).padStart(2, "0")}-${name}.png`), fullPage: false });
  log(`📸 ${String(shotIdx).padStart(2, "0")}-${name}`);
}

log(`🚀 create workspace`);
await page.goto(BASE, { waitUntil: "domcontentloaded" });
await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
await page.waitForTimeout(1000);
await page.getByRole("button", { name: "New project", exact: true }).click();
await page.waitForURL(/workspaceId=/, { timeout: 20000 });
await page.locator("textarea").first().waitFor({ state: "visible", timeout: 15000 });
await page.waitForTimeout(2500);
await shot("wizard-start");

const workspaceId = new URL(page.url()).searchParams.get("workspaceId");
log(`workspaceId=${workspaceId}`);

async function findAsk() {
  return await page.evaluate(() => {
    const dialogs = Array.from(document.querySelectorAll("[role='dialog']"));
    for (const d of dialogs.reverse()) {
      const q = d.querySelector("[id='ask-user-question']")?.textContent?.trim();
      if (!q) continue;
      const buttons = Array.from(d.querySelectorAll("button")).map((b) => {
        const rect = b.getBoundingClientRect();
        return { label: (b.querySelector("span")?.textContent || b.textContent || "").trim().slice(0, 60), disabled: b.disabled, x: Math.round(rect.x + rect.width / 2), y: Math.round(rect.y + rect.height / 2), visible: rect.width > 0 && rect.height > 0 };
      }).filter((b) => b.visible && b.label && !/^其他/.test(b.label));
      return { question: q, buttons };
    }
    return null;
  });
}

const trail = [];
const MAX = 20;

for (let round = 1; round <= MAX; round++) {
  // Wait for a dialog to appear with at least one enabled option
  const deadline = Date.now() + 120000;
  let ask = null;
  while (Date.now() < deadline) {
    ask = await findAsk();
    if (ask && ask.buttons.some((b) => !b.disabled)) break;
    await page.waitForTimeout(400);
  }
  if (!ask) {
    log(`\n=== round ${round}: NO DIALOG after 120s — wizard may be done ===`);
    await shot(`r${round}-no-dialog`);
    break;
  }
  const enabledOpts = ask.buttons.filter((b) => !b.disabled);
  log(`\n=== round ${round} ===`);
  log(`❓ ${ask.question}`);
  log(`   ${enabledOpts.length} enabled options: ${JSON.stringify(enabledOpts.map((b) => b.label))}`);
  trail.push({ round, question: ask.question, options: enabledOpts.map((b) => b.label) });
  if (enabledOpts.length === 0) {
    log(`⚠️  no enabled options — wizard might expect "其他" free-text or has finished`);
    await shot(`r${round}-no-enabled`);
    break;
  }
  const target = enabledOpts[0];
  log(`👉 click: ${target.label}`);
  await shot(`r${round}-pre-click`);
  await page.mouse.click(target.x, target.y);

  // Wait for question to change or dialog to disappear
  const advanceDeadline = Date.now() + 120000;
  let advanced = false;
  while (Date.now() < advanceDeadline) {
    await page.waitForTimeout(500);
    const next = await findAsk();
    if (!next) { advanced = true; log(`  → dialog gone (wizard may be done)`); break; }
    if (next.question !== ask.question) { advanced = true; log(`  → new question: ${next.question}`); break; }
  }
  if (!advanced) {
    log(`❌ wizard did not advance after 120s`);
    await shot(`r${round}-stuck`);
    break;
  }
  await shot(`r${round}-advanced`);
}

// Give the wizard a moment to fully settle
await page.waitForTimeout(6000);
await shot("final-state");

// ─── Observe final state ───────────────────────────────────────────────────
const finalUrl = page.url();
const finalAssistantId = new URL(finalUrl).searchParams.get("assistantId");
log(`\n=== FINAL STATE ===`);
log(`URL: ${finalUrl}`);
log(`URL assistantId: ${finalAssistantId}`);

const bodyExcerpt = await page.evaluate(() => document.body.innerText.slice(0, 800));
log(`\nbody text (first 800):\n${bodyExcerpt}`);

// Backend state
const threadsR = await fetch(`${RUNTIME}/threads/search`, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ limit: 5, metadata: { internagents_workspace_id: workspaceId } }),
});
const threads = await threadsR.json();
log(`\nthreads in workspace: ${threads.length}`);
const perThread = [];
for (const t of threads) {
  const s = await fetch(`${RUNTIME}/threads/${t.thread_id}/state`);
  const state = await s.json();
  const messages = state?.values?.messages ?? [];
  const reviews = state?.values?.reviews ?? [];
  const goal = state?.values?.goal ?? null;
  const types = messages.map((m) => m.type);
  const info = {
    thread_id: t.thread_id,
    graph_id: t.metadata?.graph_id,
    messageCount: messages.length,
    types,
    reviewCount: reviews.length,
    reviews: reviews.map((r) => ({ verdict: r.verdict, at: r.at_message_index })),
    goal: goal ? { status: goal.status, objective: (goal.objective || "").slice(0, 120) } : null,
    lastAi: (() => {
      const ai = messages.filter((m) => m.type === "ai").at(-1);
      if (!ai) return null;
      return { hasContent: !!(typeof ai.content === "string" ? ai.content : "").length, contentPreview: (typeof ai.content === "string" ? ai.content : JSON.stringify(ai.content || "")).slice(0, 200), toolCalls: (ai.tool_calls || []).map((tc) => tc.name) };
    })(),
  };
  perThread.push(info);
  log(`  thread ${info.thread_id.slice(0, 8)}: graph=${info.graph_id} msgs=${info.messageCount} reviews=${info.reviewCount} goal=${info.goal ? info.goal.status : "null"}`);
  log(`    types: ${JSON.stringify(info.types)}`);
  if (info.lastAi) log(`    lastAi: toolCalls=${JSON.stringify(info.lastAi.toolCalls)} content=${info.lastAi.contentPreview.slice(0, 100)}`);
  if (info.goal) log(`    goal: ${JSON.stringify(info.goal)}`);
}

// Check onboarding required (endpoint returns a JS snippet, not JSON — parse the embedded object)
try {
  const cfgR = await fetch(`${BASE}/api/runtime/desktop-config`);
  const raw = await cfgR.text();
  const m = raw.match(/=\s*(\{[\s\S]*?\});?\s*$/);
  const cfg = m ? JSON.parse(m[1]) : null;
  log(`\ndesktop-config.onboardingRequired = ${cfg?.onboardingRequired}`);
} catch (e) {
  log(`\ndesktop-config parse failed: ${e.message}`);
}
const wsR = await fetch(`${BASE}/api/workspaces`);
const wsList = await wsR.json();
const myWs = (wsList?.workspaces || []).find((w) => w.id === workspaceId);
log(`workspace record: ${JSON.stringify(myWs)}`);

writeFileSync(join(runDir, "summary.json"), JSON.stringify({
  workspaceId, finalUrl, finalAssistantId,
  trail, perThread, bodyExcerpt: bodyExcerpt.slice(0, 500),
  workspaceRecord: myWs,
  counts: {
    consoleErrors: events.filter((e) => e.type === "console" && e.level === "error").length,
    pageErrors: events.filter((e) => e.type === "pageerror").length,
    badResponses: events.filter((e) => e.type === "badResp").length,
  },
}, null, 2));

log(`\n=== SUMMARY: ${trail.length} rounds walked, ${threads.length} threads, out=${runDir} ===`);
await browser.close();
