import { chromium } from "playwright";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = process.env.BASE_URL || "http://127.0.0.1:3000";
const RUNTIME = process.env.RUNTIME_URL || "http://127.0.0.1:2024";
const MG_URL = process.env.MG_URL || `${BASE}/?assistantId=agent_local&resourceId=local&workspaceId=local-d15f5b3aa04d`;

const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter2-${stamp}`);
mkdirSync(runDir, { recursive: true });

const events = [];
const logPath = join(runDir, "run.log");
function log(m) {
  const line = `[${new Date().toISOString()}] ${m}`;
  console.log(line);
  appendFileSync(logPath, line + "\n");
}
function evt(t, d) { events.push({ t: new Date().toISOString(), type: t, ...d }); }

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();

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
  return label;
}

async function currentThreadId() {
  // Poll the langgraph runtime for the newest thread in mg — that's the one we just created.
  const r = await fetch(`${RUNTIME}/threads/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ limit: 5, metadata: { internagents_workspace_id: "local-d15f5b3aa04d" } }),
  });
  const list = await r.json();
  return list?.[0]?.thread_id ?? null;
}

async function fetchThreadState(tid) {
  const r = await fetch(`${RUNTIME}/threads/${tid}/state`);
  if (!r.ok) return { error: r.status };
  const state = await r.json();
  const messages = state?.values?.messages ?? [];
  const reviews = state?.values?.reviews ?? [];
  return {
    messageCount: messages.length,
    reviewCount: reviews.length,
    lastMsg: messages.length ? { type: messages.at(-1)?.type, role: messages.at(-1)?.role, contentPreview: (typeof messages.at(-1)?.content === "string" ? messages.at(-1).content : JSON.stringify(messages.at(-1)?.content ?? null)).slice(0, 150) } : null,
    reviews: reviews.map((r) => ({ verdict: r.verdict, status: r.status, at_message_index: r.at_message_index, notes: (r.notes || r.summary || "").slice(0, 100) })),
  };
}

log(`🚀 open mg: ${MG_URL}`);
await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
await page.locator("textarea").first().waitFor({ state: "visible", timeout: 15000 });
await page.waitForTimeout(1000);

// Start a fresh session so we can measure clean state
const newBtn = page.getByRole("button", { name: "New", exact: true }).first();
if (await newBtn.isVisible().catch(() => false)) {
  log(`👉 click New for fresh session`);
  await newBtn.click();
  await page.waitForTimeout(800);
}
await shot("mg-fresh");

// ─── A) Reviewer window measurement ─────────────────────────────────────────
log(`\n=== A) reviewer window (fast poll) ===`);
const REVIEW_RE = /reviewing…|reviewing\.\.\.|正在审查中/;
const ta = page.locator("textarea").first();
await ta.click();
await ta.fill("请用一句话解释：什么是引力波？");

// Set up polling BEFORE sending
const pollLog = [];
let stopPoll = false;
const pollStart = Date.now();
const pollTask = (async () => {
  while (!stopPoll) {
    const t = Date.now() - pollStart;
    try {
      const info = await page.evaluate((re) => {
        const bt = document.body.innerText;
        const hit = new RegExp(re).exec(bt);
        const sendBtn = Array.from(document.querySelectorAll("button")).some((b) => (b.textContent || "").trim() === "Send");
        const stopBtn = Array.from(document.querySelectorAll("button")).some((b) => (b.textContent || "").trim() === "Stop");
        // find any pill text with review verdicts
        const pillTexts = Array.from(document.querySelectorAll("*")).filter((el) => el.children.length === 0).map((el) => (el.textContent || "").trim()).filter((t) => /^(pass|fail|warn|unknown|reviewing…|review failed)$/.test(t));
        return { reviewing: hit?.[0] ?? null, sendBtn, stopBtn, pills: pillTexts.slice(0, 3) };
      }, REVIEW_RE.source);
      pollLog.push({ t, ...info });
    } catch { /* ignore transient */ }
    await new Promise((r) => setTimeout(r, 80));
  }
})();

log(`📤 send science prompt`);
const tSend = Date.now();
await page.getByRole("button", { name: "Send", exact: true }).click();

// Wait for Send button return (stream done)
try {
  await page.getByRole("button", { name: "Send", exact: true }).waitFor({ state: "visible", timeout: 60000 });
  log(`✅ Send returned at ${Date.now() - tSend} ms`);
} catch { log(`❌ Send never returned in 60s`); }

// Keep polling for reviewer to finish
await new Promise((r) => setTimeout(r, 6000));
stopPoll = true; await pollTask;
await shot("mg-A-final");

let firstRev = null, lastRev = null, firstPass = null;
for (const p of pollLog) {
  if (p.reviewing) { if (firstRev === null) firstRev = p.t; lastRev = p.t; }
  if (p.pills.some((x) => x === "pass" || x === "fail" || x === "warn") && firstPass === null) firstPass = p.t;
}
log(`\n🔎 reviewer window: firstReviewing=${firstRev} lastReviewing=${lastRev} durationMs=${firstRev !== null ? lastRev - firstRev : null}`);
log(`🔎 first verdict pill seen at: ${firstPass}`);

const tidA = await currentThreadId();
log(`\n🧵 thread A: ${tidA}`);
const stateA = await fetchThreadState(tidA);
log(`   state: ${JSON.stringify(stateA)}`);

// ─── B) Rapid-stop test: click Stop the moment it appears ──────────────────
log(`\n=== B) rapid stop ===`);
await ta.click();
await ta.fill("请详细介绍暗物质研究的历史，从 20 世纪 30 年代开始，尽可能长。");
const tSendB = Date.now();
await page.getByRole("button", { name: "Send", exact: true }).click();
// Wait for Stop to appear then click ASAP
const stopSelector = page.getByRole("button", { name: "Stop", exact: true });
await stopSelector.waitFor({ state: "visible", timeout: 5000 });
const tStopVisible = Date.now();
log(`✅ Stop visible after ${tStopVisible - tSendB} ms`);
await stopSelector.click();
const tStopClicked = Date.now();
log(`✋ Stop clicked at ${tStopClicked - tSendB} ms after Send`);
await shot("mg-B-just-after-stop");

// Wait for Send to return
try {
  await page.getByRole("button", { name: "Send", exact: true }).waitFor({ state: "visible", timeout: 15000 });
  log(`✅ Send returned in ${Date.now() - tStopClicked} ms after stop`);
} catch { log(`❌ Send never returned within 15s of stop`); }
await shot("mg-B-settled");
await new Promise((r) => setTimeout(r, 3000));
await shot("mg-B-3s-later");

// Snapshot both UI count and backend state
const uiCountB = await page.evaluate(() => {
  const bubbles = document.querySelectorAll("[data-role='user'], [data-role='assistant'], .ocs-message-user, .ocs-message-assistant");
  const bodyText = document.body.innerText;
  return { bubbleCount: bubbles.length, hasLongPrompt: bodyText.includes("暗物质研究的历史") };
});
log(`ui after rapid stop: ${JSON.stringify(uiCountB)}`);
const stateB = await fetchThreadState(tidA);
log(`backend state after rapid stop: ${JSON.stringify(stateB)}`);

// ─── C) Send a follow-up to see if orphan reappears ────────────────────────
log(`\n=== C) send follow-up after rapid stop ===`);
await ta.click();
await ta.fill("好的，请数到 3。");
await page.getByRole("button", { name: "Send", exact: true }).click();
try {
  await page.getByRole("button", { name: "Send", exact: true }).waitFor({ state: "visible", timeout: 60000 });
} catch { log(`❌ Send never returned in 60s`); }
await new Promise((r) => setTimeout(r, 4000));
await shot("mg-C-final");

const uiCountC = await page.evaluate(() => {
  const bodyText = document.body.innerText;
  return {
    hasLongPrompt: bodyText.includes("暗物质研究的历史"),
    hasScience: bodyText.includes("引力波"),
    hasCount: bodyText.includes("数到 3"),
    reviewPillCount: (bodyText.match(/(pass|fail|warn)/g) || []).length,
  };
});
log(`ui after follow-up: ${JSON.stringify(uiCountC)}`);
const stateC = await fetchThreadState(tidA);
log(`backend after follow-up: ${JSON.stringify(stateC)}`);

// ─── Report ────────────────────────────────────────────────────────────────
writeFileSync(join(runDir, "events.json"), JSON.stringify(events, null, 2));
writeFileSync(join(runDir, "poll.json"), JSON.stringify(pollLog, null, 2));
writeFileSync(join(runDir, "summary.json"), JSON.stringify({
  thread: tidA,
  reviewerWindow: { firstMs: firstRev, lastMs: lastRev, durationMs: firstRev !== null ? lastRev - firstRev : null, firstVerdictPillMs: firstPass },
  rapidStop: { stopVisibleMs: tStopVisible - tSendB, stopClickedMs: tStopClicked - tSendB, uiAfter: uiCountB, backendAfter: stateB },
  followUp: { uiAfter: uiCountC, backendAfter: stateC },
  counts: {
    consoleErrors: events.filter((e) => e.type === "console" && e.level === "error").length,
    pageErrors: events.filter((e) => e.type === "pageerror").length,
    badResponses: events.filter((e) => e.type === "badResp").length,
    requestFailures: events.filter((e) => e.type === "reqFail").length,
  },
}, null, 2));
log(`\nout: ${runDir}`);
await browser.close();
