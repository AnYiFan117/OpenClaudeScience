// Iter 8: complete wizard, send free-form science answer, observe goal/assistant/reviewer
import { chromium } from "playwright";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = "http://127.0.0.1:3000";
const RUNTIME = "http://127.0.0.1:2024";
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter8-post-wizard-${stamp}`);
mkdirSync(runDir, { recursive: true });

const wsPath = `/tmp/e2e_iter8_${Date.now()}`;
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
async function fetchThreadState(tid) {
  const s = await fetch(`${RUNTIME}/threads/${tid}/state`);
  const state = await s.json();
  const messages = state?.values?.messages ?? [];
  const reviews = state?.values?.reviews ?? [];
  return {
    messageCount: messages.length,
    types: messages.map((m) => m.type),
    toolCalls: messages.flatMap((m) => (m.tool_calls || []).map((tc) => tc.name)),
    reviewCount: reviews.length,
    reviews: reviews.map((r) => ({ verdict: r.verdict, at: r.at_message_index, issues: (r.issues || []).length, suggestions: (r.suggestions || []).length })),
    goal: state?.values?.goal ?? null,
    lastAi: (() => {
      const ai = messages.filter((m) => m.type === "ai").at(-1);
      if (!ai) return null;
      const content = typeof ai.content === "string" ? ai.content : JSON.stringify(ai.content || "");
      return { hasText: content.length > 0, preview: content.slice(0, 200), toolCalls: (ai.tool_calls || []).map((tc) => tc.name) };
    })(),
  };
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

// Walk wizard until dialog goes away (invitation phase)
log(`\n=== A) walk wizard ===`);
for (let round = 1; round <= 10; round++) {
  const deadline = Date.now() + 90000;
  let ask = null;
  while (Date.now() < deadline) {
    ask = await findAsk();
    if (ask && ask.buttons.some((b) => !b.disabled)) break;
    await page.waitForTimeout(400);
  }
  if (!ask) { log(`round ${round}: no dialog — wizard done`); break; }
  const target = ask.buttons.find((b) => !b.disabled);
  log(`R${round} Q: "${ask.question.slice(0, 40)}..." → click "${target.label}"`);
  const prev = ask.question;
  await page.mouse.click(target.x, target.y);
  const adv = Date.now() + 90000;
  while (Date.now() < adv) {
    await page.waitForTimeout(500);
    const n = await findAsk();
    if (!n) break;
    if (n.question !== prev) break;
  }
}
await page.waitForTimeout(5000);
await shot("post-wizard-invitation");

// Backend snapshot after wizard walk
const threadsR = await fetch(`${RUNTIME}/threads/search`, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ limit: 1, metadata: { internagents_workspace_id: workspaceId } }),
});
const [thread] = await threadsR.json();
const tid = thread.thread_id;
log(`\nthread: ${tid} graph=${thread.metadata?.graph_id}`);
const stateA = await fetchThreadState(tid);
log(`state after wizard walk:`);
log(`  msgs=${stateA.messageCount} types=${JSON.stringify(stateA.types)}`);
log(`  toolCalls=${JSON.stringify(stateA.toolCalls)}`);
log(`  reviews=${stateA.reviewCount} ${JSON.stringify(stateA.reviews)}`);
log(`  goal=${stateA.goal ? JSON.stringify({ status: stateA.goal.status, objective: (stateA.goal.objective || "").slice(0, 60) }) : "null"}`);

// ─── B) Send science answer to the invitation ──────────────────────────────
log(`\n=== B) send science-topic answer to invitation ===`);
const answer = "我目前在做超音速气动数据的处理：有 shock wave 结构的风洞实测（几百个 case）和 CFD 仿真结果的配对，想先做数据清洗+建库，后面对比实测与仿真的偏差。";
const ta = page.locator("textarea").first();
await ta.click();
await ta.fill(answer);
const tSend = Date.now();
await page.getByRole("button", { name: "Send", exact: true }).click();
log(`📤 sent answer`);

// Wait up to 120s for reply
try {
  await page.getByRole("button", { name: "Send", exact: true }).waitFor({ state: "visible", timeout: 120000 });
  log(`✅ reply arrived in ${Date.now() - tSend}ms`);
} catch { log(`❌ no reply in 120s`); }
await page.waitForTimeout(8000);
await shot("after-science-answer");

const stateB = await fetchThreadState(tid);
log(`\nstate after science answer:`);
log(`  msgs=${stateB.messageCount} types=${JSON.stringify(stateB.types)}`);
log(`  toolCalls=${JSON.stringify(stateB.toolCalls)}`);
log(`  reviews=${stateB.reviewCount} ${JSON.stringify(stateB.reviews)}`);
log(`  goal=${stateB.goal ? JSON.stringify({ status: stateB.goal.status, objective: (stateB.goal.objective || "").slice(0, 100) }) : "null"}`);
if (stateB.lastAi) log(`  lastAi preview: ${stateB.lastAi.preview.slice(0, 200)}`);

// Was the thread's graph promoted from onboarding to normal?
const threadsR2 = await fetch(`${RUNTIME}/threads/search`, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ limit: 1, metadata: { internagents_workspace_id: workspaceId } }),
});
const [thread2] = await threadsR2.json();
log(`\nthread graph after answer: ${thread2.metadata?.graph_id}`);
const url2 = page.url();
log(`URL: ${url2}`);

writeFileSync(join(runDir, "summary.json"), JSON.stringify({
  workspaceId, thread_id: tid,
  postWizard: stateA,
  postAnswer: stateB,
  urlAfter: url2,
  graphAfter: thread2.metadata?.graph_id,
  counts: {
    consoleErrors: events.filter((e) => e.type === "console" && e.level === "error").length,
    pageErrors: events.filter((e) => e.type === "pageerror").length,
    badResponses: events.filter((e) => e.type === "badResp").length,
  },
}, null, 2));

log(`\nout: ${runDir}`);
await browser.close();
