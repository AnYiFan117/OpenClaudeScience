// Iter 11: (A) 10-turn conversation in mg, watch stability. (B) stop during long answer.
import { chromium } from "playwright";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = "http://127.0.0.1:3000";
const RUNTIME = "http://127.0.0.1:2024";
const MG_URL = `${BASE}/?assistantId=agent_local&resourceId=local&workspaceId=local-d15f5b3aa04d`;
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter11-${stamp}`);
mkdirSync(runDir, { recursive: true });

const events = [];
function log(m) { const l = `[${new Date().toISOString()}] ${m}`; console.log(l); appendFileSync(join(runDir, "run.log"), l + "\n"); }
function evt(t, d) { events.push({ t: new Date().toISOString(), type: t, ...d }); }

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
page.on("console", (m) => evt("console", { level: m.type(), text: m.text().slice(0, 400) }));
page.on("pageerror", (e) => evt("pageerror", { message: e.message }));
page.on("response", (r) => { if (r.status() >= 400) evt("badResp", { url: r.url(), status: r.status() }); });

let shotIdx = 0;
async function shot(name) {
  shotIdx += 1;
  await page.screenshot({ path: join(runDir, `${String(shotIdx).padStart(2, "0")}-${name}.png`), fullPage: false });
  log(`📸 ${String(shotIdx).padStart(2, "0")}-${name}`);
}

async function currentThreadId() {
  return new URL(page.url()).searchParams.get("threadId");
}
async function fetchThreadState(tid) {
  const r = await fetch(`${RUNTIME}/threads/${tid}/state`);
  if (!r.ok) return null;
  const s = await r.json();
  return {
    messageCount: (s?.values?.messages ?? []).length,
    reviewCount: (s?.values?.reviews ?? []).length,
    lastAi: (() => {
      const ai = (s?.values?.messages ?? []).findLast?.((m) => m?.type === "ai") || null;
      if (!ai) return null;
      const c = typeof ai.content === "string" ? ai.content : JSON.stringify(ai.content || "");
      return { len: c.length, preview: c.slice(0, 100) };
    })(),
  };
}
async function pageMetrics() {
  return await page.evaluate(() => ({
    domNodes: document.querySelectorAll("*").length,
    messageBubbles: document.querySelectorAll("[data-role='user'], [data-role='assistant'], .ocs-message-user, .ocs-message-assistant").length,
    bodyHeight: document.body.scrollHeight,
    memMB: typeof performance !== "undefined" && (performance).memory ? Math.round((performance).memory.usedJSHeapSize / 1024 / 1024) : null,
  }));
}

log(`🚀 open mg`);
await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
await page.locator("textarea").first().waitFor({ state: "visible", timeout: 15000 });
await page.waitForTimeout(2000);

// Start a fresh session
const newBtn = page.getByRole("button", { name: "New", exact: true }).first();
if (await newBtn.isVisible().catch(() => false)) {
  await newBtn.click();
  await page.waitForTimeout(1500);
}
await shot("mg-fresh");

const ta = page.locator("textarea").first();

// ─── A) 10 short turns ─────────────────────────────────────────────────────
log(`\n=== A) 10 short science turns ===`);
const prompts = [
  "一句话：什么是熵？",
  "一句话：什么是黑洞蒸发？",
  "一句话：什么是量子纠缠？",
  "一句话：什么是宇宙微波背景辐射？",
  "一句话：什么是相变？",
  "一句话：什么是奇异物质？",
  "一句话：什么是宇宙学常数问题？",
  "一句话：什么是中微子振荡？",
  "一句话：什么是霍金辐射？",
  "一句话：什么是普朗克尺度？",
];
const turnStats = [];
for (const [i, p] of prompts.entries()) {
  await ta.click();
  await ta.fill(p);
  const t0 = Date.now();
  await page.getByRole("button", { name: "Send", exact: true }).click();
  try {
    await page.getByRole("button", { name: "Send", exact: true }).waitFor({ state: "visible", timeout: 60000 });
    const dt = Date.now() - t0;
    const metrics = await pageMetrics();
    turnStats.push({ turn: i + 1, replyMs: dt, ...metrics });
    log(`  R${i+1}: reply=${dt}ms domNodes=${metrics.domNodes} bodyH=${metrics.bodyHeight} mem=${metrics.memMB}MB`);
  } catch {
    log(`  R${i+1}: TIMEOUT`);
    turnStats.push({ turn: i + 1, replyMs: -1 });
    break;
  }
}
await page.waitForTimeout(5000);
await shot("A-10-turns-done");

const tidA = await currentThreadId();
log(`\nthread A: ${tidA}`);
const stateA = await fetchThreadState(tidA);
log(`state A: ${JSON.stringify(stateA)}`);

// Trend check
if (turnStats.length >= 3) {
  const first3 = turnStats.slice(0, 3).map((s) => s.replyMs);
  const last3 = turnStats.slice(-3).map((s) => s.replyMs);
  log(`replyMs first-3=${JSON.stringify(first3)} last-3=${JSON.stringify(last3)}`);
  const firstDom = turnStats[0].domNodes;
  const lastDom = turnStats.at(-1).domNodes;
  log(`domNodes first=${firstDom} last=${lastDom} delta=${lastDom - firstDom}`);
}

// ─── B) stop during long generation ─────────────────────────────────────────
log(`\n=== B) stop mid-long-answer ===`);
await ta.click();
await ta.fill("请写一段 300 字左右的关于星际尘埃在恒星形成中作用的科普介绍。");
const tSendB = Date.now();
await page.getByRole("button", { name: "Send", exact: true }).click();
await page.getByRole("button", { name: "Stop", exact: true }).waitFor({ state: "visible", timeout: 10000 });
log(`  Stop visible ${Date.now() - tSendB}ms after Send`);

// Let it stream for ~5s, then stop
await page.waitForTimeout(5000);
await shot("B-mid-stream");

const stopBtn = page.getByRole("button", { name: "Stop", exact: true });
if (await stopBtn.isVisible().catch(() => false)) {
  log(`  ✋ click Stop after 5s of streaming`);
  await stopBtn.click();
} else {
  log(`  ⚠️  Stop button not visible when we tried to click (assistant may have finished)`);
}
await page.waitForTimeout(500);
await shot("B-just-stopped");

// Wait for UI to settle to Send
try {
  await page.getByRole("button", { name: "Send", exact: true }).waitFor({ state: "visible", timeout: 15000 });
  log(`  ✅ UI settled to Send`);
} catch { log(`  ❌ UI did not return to Send within 15s`); }
await page.waitForTimeout(3000);
await shot("B-settled");

const stateB = await fetchThreadState(tidA);
log(`state B (after stop): ${JSON.stringify(stateB)}`);
if (stateB && stateA) {
  const msgDelta = stateB.messageCount - stateA.messageCount;
  log(`msg count delta: ${msgDelta} (${stateA.messageCount} → ${stateB.messageCount})`);
  if (stateB.lastAi) log(`last AI content len=${stateB.lastAi.len} preview="${stateB.lastAi.preview}"`);
}

// ─── C) send follow-up after stop, verify recovery ─────────────────────────
log(`\n=== C) follow-up after stop ===`);
await ta.click();
await ta.fill("好的，谢谢，请数到 3。");
const tSendC = Date.now();
await page.getByRole("button", { name: "Send", exact: true }).click();
try {
  await page.getByRole("button", { name: "Send", exact: true }).waitFor({ state: "visible", timeout: 60000 });
  log(`  ✅ C reply in ${Date.now() - tSendC}ms`);
} catch { log(`  ❌ C timed out`); }
await page.waitForTimeout(4000);
await shot("C-final");
const stateC = await fetchThreadState(tidA);
log(`state C: ${JSON.stringify(stateC)}`);

writeFileSync(join(runDir, "summary.json"), JSON.stringify({
  thread: tidA, turnStats, stateA, stateB, stateC,
  counts: {
    consoleErrors: events.filter((e) => e.type === "console" && e.level === "error").length,
    pageErrors: events.filter((e) => e.type === "pageerror").length,
    badResponses: events.filter((e) => e.type === "badResp").length,
  },
}, null, 2));

log(`\nout: ${runDir}`);
await browser.close();
