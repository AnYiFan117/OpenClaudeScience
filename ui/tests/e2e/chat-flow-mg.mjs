import { chromium } from "playwright";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = process.env.BASE_URL || "http://127.0.0.1:3000";
// Discovery told us mg lives at this workspaceId. Hardcoded for now — it's derived from disk path hash and stable per user.
const MG_URL = process.env.MG_URL || `${BASE}/?assistantId=agent_local&resourceId=local&workspaceId=local-d15f5b3aa04d`;

const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const runDir = join(outDir, `chat-flow-${stamp}`);
mkdirSync(runDir, { recursive: true });

const events = []; // structured timeline
const logPath = join(runDir, "run.log");
function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}`;
  console.log(line);
  appendFileSync(logPath, line + "\n");
}
function evt(type, detail) {
  events.push({ t: new Date().toISOString(), type, ...detail });
}

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();

page.on("console", (m) => evt("console", { level: m.type(), text: m.text().slice(0, 500) }));
page.on("pageerror", (e) => evt("pageerror", { message: e.message, stack: (e.stack || "").slice(0, 800) }));
page.on("response", (r) => { if (r.status() >= 400) evt("badResp", { url: r.url(), status: r.status(), method: r.request().method() }); });
page.on("requestfailed", (r) => evt("reqFail", { url: r.url(), method: r.method(), reason: r.failure()?.errorText }));

let shotIdx = 0;
async function shot(name) {
  shotIdx += 1;
  const label = `${String(shotIdx).padStart(2, "0")}-${name}`;
  const file = join(runDir, `${label}.png`);
  await page.screenshot({ path: file, fullPage: false });
  log(`📸 ${label}`);
  return label;
}

async function snapshotState(tag) {
  // Grab quick signals for later analysis
  const state = await page.evaluate(() => {
    const buttons = Array.from(document.querySelectorAll("button")).map((b) => {
      const rect = b.getBoundingClientRect();
      return rect.width && rect.height ? { text: (b.textContent || "").trim().slice(0, 40), disabled: b.disabled, x: Math.round(rect.x), y: Math.round(rect.y) } : null;
    }).filter(Boolean);
    const ta = document.querySelector("textarea");
    const composerPlaceholder = ta?.getAttribute("placeholder") || null;
    const composerValue = ta?.value ?? null;
    // Any "Reviewing" or 正在审查 text on the page?
    const bodyText = document.body.innerText;
    const reviewing = /Reviewing\.\.\.|正在审查中/.test(bodyText);
    // Rough count of message bubbles — assistant vs user
    const userMsgs = document.querySelectorAll("[data-role='user'], .ocs-message-user").length;
    const asstMsgs = document.querySelectorAll("[data-role='assistant'], .ocs-message-assistant").length;
    // Try to find a review card presence heuristic
    const reviewCards = Array.from(document.querySelectorAll("*")).filter((el) => /Review\b|审查/.test(el.textContent || "")).length;
    return { composerPlaceholder, composerValue, reviewing, userMsgs, asstMsgs, buttonCount: buttons.length, sendVisible: buttons.some(b => b.text === "Send"), stopVisible: buttons.some(b => b.text === "Stop"), reviewCards };
  });
  evt("state", { tag, ...state });
  log(`🧭 ${tag}: send=${state.sendVisible} stop=${state.stopVisible} reviewing=${state.reviewing} placeholder="${state.composerPlaceholder}"`);
  return state;
}

async function waitForStopButton(ms = 8000) {
  const stop = page.getByRole("button", { name: "Stop", exact: true });
  await stop.waitFor({ state: "visible", timeout: ms });
  return stop;
}

async function waitForSendButton(ms = 60000) {
  const send = page.getByRole("button", { name: "Send", exact: true });
  await send.waitFor({ state: "visible", timeout: ms });
  return send;
}

async function typeAndSend(text, { waitForReply = true, waitForReplyMs = 90000 } = {}) {
  const ta = page.locator("textarea").first();
  await ta.click();
  await ta.fill(text);
  const send = page.getByRole("button", { name: "Send", exact: true });
  await send.click();
  log(`📤 sent: ${JSON.stringify(text)}`);
  if (waitForReply) {
    await waitForSendButton(waitForReplyMs).catch(() => log(`⚠️  waitForSendButton timeout after ${waitForReplyMs}ms`));
  }
}

// ─── Navigate to mg ────────────────────────────────────────────────────────
log(`🚀 goto ${MG_URL}`);
await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
await page.locator("textarea").first().waitFor({ state: "visible", timeout: 15000 });
await page.waitForTimeout(1200);
await shot("mg-loaded");
await snapshotState("mg-loaded");

// Start a fresh conversation by clicking "New" (top of sidebar)
const newBtn = page.getByRole("button", { name: "New", exact: true }).first();
if (await newBtn.isVisible().catch(() => false)) {
  log(`👉 clicking sidebar New to start fresh conversation`);
  await newBtn.click();
  await page.waitForTimeout(800);
  await shot("after-new");
}

// ─── Round 1: short prompt, expect full review flow ────────────────────────
log(`\n=== ROUND 1: short prompt → observe reviewer ===`);
await typeAndSend("请一句话介绍你自己。", { waitForReplyMs: 60000 });
// After Send button returns, snapshot; assistant may already be done or reviewer may be running
await shot("r1-after-reply");
const r1a = await snapshotState("r1-after-reply");

// Poll for review card / reviewing indicator up to 40s
const r1PollDeadline = Date.now() + 40000;
let sawReviewing = r1a.reviewing;
while (Date.now() < r1PollDeadline) {
  await page.waitForTimeout(1500);
  const s = await snapshotState("r1-poll");
  if (s.reviewing) sawReviewing = true;
  if (!s.reviewing && sawReviewing) { log(`🎯 R1: reviewing cleared`); break; }
  if (!s.reviewing && !sawReviewing && (Date.now() - r1PollDeadline + 40000) > 8000) {
    // If we never saw reviewing after 8s of polling, bail early
    log(`ℹ️  R1: no reviewing indicator observed`);
    break;
  }
}
await shot("r1-final");
await snapshotState("r1-final");

// ─── Round 2: long prompt, then STOP mid-generation ────────────────────────
log(`\n=== ROUND 2: long prompt → stop mid-generation ===`);
const longPrompt = "请写一首至少 40 行的关于宇宙、星辰与航天的长诗，用富有意象和押韵的中文。";
const ta = page.locator("textarea").first();
await ta.click();
await ta.fill(longPrompt);
await page.getByRole("button", { name: "Send", exact: true }).click();
log(`📤 sent long prompt`);

// Wait for Stop button to appear (streaming started)
try {
  await waitForStopButton(15000);
  log(`✅ Stop button visible — streaming has started`);
} catch (e) {
  log(`❌ Stop button never appeared: ${e.message}`);
}
await shot("r2-streaming");
await snapshotState("r2-streaming");

// Let it stream a bit
await page.waitForTimeout(3000);
await shot("r2-mid-stream");
await snapshotState("r2-mid-stream");

// Click Stop
const stopBtn = page.getByRole("button", { name: "Stop", exact: true });
if (await stopBtn.isVisible().catch(() => false)) {
  log(`✋ clicking Stop`);
  await stopBtn.click();
  await page.waitForTimeout(500);
  await shot("r2-just-after-stop");
  await snapshotState("r2-just-after-stop");
} else {
  log(`⚠️  Stop button not visible when we tried to click`);
}

// Wait for UI to settle back to idle (Send visible)
try {
  await waitForSendButton(20000);
  log(`✅ UI back to Send button`);
} catch {
  log(`❌ UI never returned to Send button within 20s`);
}
await shot("r2-settled");
await snapshotState("r2-settled");

// Poll a few seconds to see if reviewer still runs after stop
for (let i = 0; i < 6; i++) {
  await page.waitForTimeout(2000);
  await snapshotState(`r2-poll-${i}`);
}
await shot("r2-post-poll");

// ─── Round 3: send after stop → verify UI recovered ────────────────────────
log(`\n=== ROUND 3: send after stop → verify recovery ===`);
await typeAndSend("好的，谢谢，请数到 3。", { waitForReplyMs: 60000 });
await shot("r3-after-reply");
await snapshotState("r3-after-reply");

// One more poll for reviewer on R3
await page.waitForTimeout(6000);
await shot("r3-final");
await snapshotState("r3-final");

// ─── Report ─────────────────────────────────────────────────────────────────
const consoleErrors = events.filter((e) => e.type === "console" && e.level === "error");
const consoleWarns = events.filter((e) => e.type === "console" && e.level === "warning");
const pageErrs = events.filter((e) => e.type === "pageerror");
const badResps = events.filter((e) => e.type === "badResp");
const reqFails = events.filter((e) => e.type === "reqFail");
const stateSnaps = events.filter((e) => e.type === "state");

writeFileSync(join(runDir, "events.json"), JSON.stringify(events, null, 2));
const summary = {
  url: page.url(),
  runDir,
  counts: {
    consoleErrors: consoleErrors.length,
    consoleWarnings: consoleWarns.length,
    pageErrors: pageErrs.length,
    badResponses: badResps.length,
    requestFailures: reqFails.length,
    stateSnapshots: stateSnaps.length,
  },
  consoleErrors,
  pageErrors: pageErrs,
  badResponses: badResps,
  requestFailures: reqFails,
};
writeFileSync(join(runDir, "summary.json"), JSON.stringify(summary, null, 2));

log(`\n=== SUMMARY ===`);
log(`console errors:   ${consoleErrors.length}`);
log(`console warnings: ${consoleWarns.length}`);
log(`page errors:      ${pageErrs.length}`);
log(`bad responses:    ${badResps.length}`);
log(`request failures: ${reqFails.length}`);
log(`out: ${runDir}`);

await browser.close();
