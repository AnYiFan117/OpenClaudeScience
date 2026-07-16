import { chromium } from "playwright";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = process.env.BASE_URL || "http://127.0.0.1:3000";
const RUNTIME = process.env.RUNTIME_URL || "http://127.0.0.1:2024";
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter5-onboarding-${stamp}`);
mkdirSync(runDir, { recursive: true });

const wsSlug = `e2e_iter5_${Date.now()}`;
const wsPath = `/tmp/${wsSlug}`;
mkdirSync(wsPath, { recursive: true });

const events = [];
const logPath = join(runDir, "run.log");
function log(m) { const l = `[${new Date().toISOString()}] ${m}`; console.log(l); appendFileSync(logPath, l + "\n"); }
function evt(t, d) { events.push({ t: new Date().toISOString(), type: t, ...d }); }

log(`🆕 workspace path: ${wsPath}`);

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();

page.on("dialog", async (d) => { log(`🗨️  dialog: ${d.message().slice(0, 100)}`); if (d.type() === "prompt") await d.accept(wsPath); else await d.accept(); });
page.on("console", (m) => evt("console", { level: m.type(), text: m.text().slice(0, 500) }));
page.on("pageerror", (e) => evt("pageerror", { message: e.message }));
page.on("response", (r) => { if (r.status() >= 400) evt("badResp", { url: r.url(), status: r.status() }); });
page.on("requestfailed", (r) => evt("reqFail", { url: r.url(), reason: r.failure()?.errorText }));

let shotIdx = 0;
async function shot(name) {
  shotIdx += 1;
  const label = `${String(shotIdx).padStart(2, "0")}-${name}`;
  await page.screenshot({ path: join(runDir, `${label}.png`), fullPage: false });
  log(`📸 ${label}`);
}

// ─── Onboard ────────────────────────────────────────────────────────────────
log(`🚀 landing → New project`);
await page.goto(BASE, { waitUntil: "domcontentloaded" });
await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
await page.waitForTimeout(1000);
await page.getByRole("button", { name: "New project", exact: true }).click();
await page.waitForURL(/workspaceId=/, { timeout: 20000 });
await page.locator("textarea").first().waitFor({ state: "visible", timeout: 15000 });
await page.waitForTimeout(2000);
await shot("wizard-start");

// ─── Walk the wizard: pick first option each round, until settled ──────────
const trail = [];
const knownOptions = new Set();

async function findAskUserOptions() {
  return await page.evaluate(() => {
    const dialogs = Array.from(document.querySelectorAll("[role='dialog']"));
    const results = [];
    for (const d of dialogs) {
      const question = d.querySelector("[id='ask-user-question']")?.textContent?.trim() || null;
      if (!question) continue;
      const buttons = Array.from(d.querySelectorAll("button")).map((b) => {
        const rect = b.getBoundingClientRect();
        return { label: (b.querySelector("span")?.textContent || b.textContent || "").trim().slice(0, 80), disabled: b.disabled, x: Math.round(rect.x + rect.width / 2), y: Math.round(rect.y + rect.height / 2), visible: rect.width > 0 && rect.height > 0 };
      }).filter((b) => b.visible && b.label && !/^其他/.test(b.label));
      results.push({ question, buttons });
    }
    return results;
  });
}

const MAX_ROUNDS = 8;
for (let round = 1; round <= MAX_ROUNDS; round++) {
  log(`\n=== round ${round} ===`);
  // Wait for any streaming to settle first (Send button visible OR ask_user visible)
  const settledDeadline = Date.now() + 60000;
  while (Date.now() < settledDeadline) {
    const st = await page.evaluate(() => {
      const sendBtn = Array.from(document.querySelectorAll("button")).some((b) => (b.textContent || "").trim() === "Send");
      const stopBtn = Array.from(document.querySelectorAll("button")).some((b) => (b.textContent || "").trim() === "Stop");
      const dialog = document.querySelector("[role='dialog']") != null;
      return { sendBtn, stopBtn, dialog };
    });
    if ((st.sendBtn && !st.stopBtn) || st.dialog) break;
    await page.waitForTimeout(500);
  }

  const asks = await findAskUserOptions();
  if (asks.length === 0) {
    log(`ℹ️  no ask_user card visible → check if wizard is done`);
    await shot(`r${round}-no-dialog`);
    break;
  }

  // Take the LAST (most recent) ask_user dialog on the page
  const ask = asks[asks.length - 1];
  log(`❓ question: ${ask.question}`);
  log(`   options: ${JSON.stringify(ask.buttons.map((b) => b.label))}`);
  trail.push({ round, question: ask.question, options: ask.buttons.map((b) => b.label) });

  // Pick the first enabled option we haven't picked before (or first)
  const target = ask.buttons.find((b) => !b.disabled && !knownOptions.has(b.label)) || ask.buttons.find((b) => !b.disabled);
  if (!target) {
    log(`❌ no clickable option`);
    await shot(`r${round}-no-option`);
    break;
  }
  knownOptions.add(target.label);
  log(`👉 clicking: ${target.label}`);

  await shot(`r${round}-before-click`);
  await page.mouse.click(target.x, target.y);
  await page.waitForTimeout(500);
  await shot(`r${round}-just-clicked`);

  // Wait for next state (streaming starts or new dialog appears)
  await page.waitForTimeout(3000);
  await shot(`r${round}-3s`);
}

// Wait a bit more for anything to settle
await page.waitForTimeout(5000);
await shot("final-state");

// ─── Query backend to see what got created ─────────────────────────────────
const workspaceId = new URL(page.url()).searchParams.get("workspaceId");
log(`\n🧵 workspace: ${workspaceId}`);
const threadsR = await fetch(`${RUNTIME}/threads/search`, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ limit: 5, metadata: { internagents_workspace_id: workspaceId } }),
});
const threadsList = await threadsR.json();
log(`threads in workspace: ${threadsList.length}`);
for (const t of threadsList) {
  const st = await fetch(`${RUNTIME}/threads/${t.thread_id}/state`);
  if (!st.ok) continue;
  const state = await st.json();
  const messages = state?.values?.messages ?? [];
  const reviews = state?.values?.reviews ?? [];
  const goal = state?.values?.goal ?? null;
  log(`  thread ${t.thread_id}: msgs=${messages.length} reviews=${reviews.length} goal=${goal ? JSON.stringify({ status: goal.status, objective: (goal.objective || "").slice(0, 80) }) : "null"} graph=${t.metadata?.graph_id}`);
}

// ─── Report ────────────────────────────────────────────────────────────────
writeFileSync(join(runDir, "events.json"), JSON.stringify(events, null, 2));
writeFileSync(join(runDir, "summary.json"), JSON.stringify({
  workspacePath: wsPath,
  workspaceId,
  trail,
  counts: {
    consoleErrors: events.filter((e) => e.type === "console" && e.level === "error").length,
    pageErrors: events.filter((e) => e.type === "pageerror").length,
    badResponses: events.filter((e) => e.type === "badResp").length,
    requestFailures: events.filter((e) => e.type === "reqFail").length,
  },
}, null, 2));

log(`\n=== SUMMARY ===`);
log(`rounds walked: ${trail.length}`);
log(`console errors: ${events.filter((e) => e.type === "console" && e.level === "error").length}`);
log(`page errors: ${events.filter((e) => e.type === "pageerror").length}`);
log(`out: ${runDir}`);
await browser.close();
