import { chromium } from "playwright";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = process.env.BASE_URL || "http://127.0.0.1:3000";
const RUNTIME = process.env.RUNTIME_URL || "http://127.0.0.1:2024";
const MG_URL = `${BASE}/?assistantId=agent_local&resourceId=local&workspaceId=local-d15f5b3aa04d`;

const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter3-${stamp}`);
mkdirSync(runDir, { recursive: true });

const events = [];
const logPath = join(runDir, "run.log");
function log(m) {
  const line = `[${new Date().toISOString()}] ${m}`;
  console.log(line); appendFileSync(logPath, line + "\n");
}
function evt(t, d) { events.push({ t: new Date().toISOString(), type: t, ...d }); }

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
page.on("console", (m) => evt("console", { level: m.type(), text: m.text().slice(0, 600) }));
page.on("pageerror", (e) => evt("pageerror", { message: e.message }));
page.on("response", (r) => { if (r.status() >= 400) evt("badResp", { url: r.url(), status: r.status() }); });
page.on("requestfailed", (r) => evt("reqFail", { url: r.url(), reason: r.failure()?.errorText }));

let shotIdx = 0;
async function shot(name) {
  shotIdx += 1;
  const label = `${String(shotIdx).padStart(2, "0")}-${name}`;
  await page.screenshot({ path: join(runDir, `${label}.png`), fullPage: false });
  log(`📸 ${label}`); return label;
}

async function currentThreadId() {
  const r = await fetch(`${RUNTIME}/threads/search`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ limit: 5, metadata: { internagents_workspace_id: "local-d15f5b3aa04d" } }),
  });
  return (await r.json())?.[0]?.thread_id ?? null;
}
async function fetchThreadState(tid) {
  const r = await fetch(`${RUNTIME}/threads/${tid}/state`);
  if (!r.ok) return { error: r.status };
  const s = await r.json();
  const messages = s?.values?.messages ?? [];
  const reviews = s?.values?.reviews ?? [];
  return {
    messageCount: messages.length,
    reviewCount: reviews.length,
    messageTypes: messages.map((m) => m?.type),
    toolCallCount: messages.reduce((acc, m) => acc + ((m?.tool_calls?.length) || 0), 0),
    reviews: reviews.map((r) => ({ verdict: r.verdict, status: r.status, at_message_index: r.at_message_index, summary: (r.summary || r.notes || "").slice(0, 120) })),
  };
}

log(`🚀 open mg: ${MG_URL}`);
await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
await page.locator("textarea").first().waitFor({ state: "visible", timeout: 15000 });
await page.waitForTimeout(1000);

// ─── A) Probe Next.js dev-tools "Issue" badge content ─────────────────────
log(`\n=== A) probe Next dev-tools Issue ===`);
const devToolInfo = await page.evaluate(() => {
  const info = { host: null, text: null, shadowText: null };
  const host = document.querySelector("nextjs-portal, [data-nextjs-toast], [id^='__next']");
  info.host = host?.tagName ?? null;
  // Try to find the "Issue" badge in the whole DOM (shadow roots included)
  const walker = (root, depth = 0) => {
    if (!root || depth > 6) return null;
    if (root.shadowRoot) {
      const t = root.shadowRoot.textContent || "";
      if (/Issue/.test(t)) return t.slice(0, 400);
      for (const child of root.shadowRoot.querySelectorAll("*")) {
        const r = walker(child, depth + 1);
        if (r) return r;
      }
    }
    for (const child of root.children) {
      const r = walker(child, depth + 1);
      if (r) return r;
    }
    return null;
  };
  info.shadowText = walker(document.body);
  // Also check main document text (unlikely — the badge is in a shadow root)
  const bodyText = document.body.innerText;
  const m = bodyText.match(/(\d+ )?Issue[s]?[^\n]*/);
  info.text = m ? m[0].slice(0, 300) : null;
  return info;
});
log(`Next dev-tools scan: ${JSON.stringify(devToolInfo)}`);

// ─── B) Fire a tool-using science-y task ────────────────────────────────────
log(`\n=== B) send tool-use task ===`);
// Start a fresh session (we saw New opens onboarding; use the mg URL "New" button as before)
const newBtn = page.getByRole("button", { name: "New", exact: true }).first();
if (await newBtn.isVisible().catch(() => false)) {
  await newBtn.click();
  await page.waitForTimeout(800);
}
await shot("mg-fresh-B");

const ta = page.locator("textarea").first();
await ta.click();
// Ask for a real filesystem operation. mg workspace has an output/ folder.
await ta.fill("请调用工具列出当前工作区 output/ 目录下的文件（前 10 个即可），然后用一句话说说都是什么。");

const REVIEW_RE = /reviewing…|reviewing\.\.\.|正在审查中/;
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
        const stopBtn = Array.from(document.querySelectorAll("button")).some((b) => (b.textContent || "").trim() === "Stop");
        const sendBtn = Array.from(document.querySelectorAll("button")).some((b) => (b.textContent || "").trim() === "Send");
        const runningPh = bt.includes("Running...") || bt.includes("Running…");
        const anyPass = /\bpass\b/i.test(bt);
        return { reviewing: hit?.[0] ?? null, stopBtn, sendBtn, runningPh, anyPass };
      }, REVIEW_RE.source);
      pollLog.push({ t, ...info });
    } catch {}
    await new Promise((r) => setTimeout(r, 100));
  }
})();

const tSend = Date.now();
log(`📤 send tool-use prompt`);
await page.getByRole("button", { name: "Send", exact: true }).click();
try {
  await page.getByRole("button", { name: "Send", exact: true }).waitFor({ state: "visible", timeout: 120000 });
  log(`✅ Send returned in ${Date.now() - tSend} ms`);
} catch { log(`❌ Send did not return in 120s`); }
await new Promise((r) => setTimeout(r, 8000));
stopPoll = true; await pollTask;
await shot("mg-B-final");

// timing
let firstRev = null, lastRev = null, firstPass = null;
for (const p of pollLog) {
  if (p.reviewing) { if (firstRev === null) firstRev = p.t; lastRev = p.t; }
  if (p.anyPass && firstPass === null) firstPass = p.t;
}
log(`🔎 reviewer window: firstReviewing=${firstRev} lastReviewing=${lastRev} durationMs=${firstRev !== null ? lastRev - firstRev : null}`);
log(`🔎 first "pass" pill in DOM at: ${firstPass}`);

const tidB = await currentThreadId();
log(`🧵 thread B: ${tidB}`);
const stateB = await fetchThreadState(tidB);
log(`state B: ${JSON.stringify(stateB)}`);

// ─── C) Rapid-fire: send 3 quick messages back-to-back ─────────────────────
log(`\n=== C) rapid-fire test ===`);
const rapidPrompts = [
  "一句话：什么是暗能量？",
  "一句话：什么是黑洞视界？",
  "一句话：什么是重子声波振荡？",
];
for (const [i, p] of rapidPrompts.entries()) {
  await ta.click();
  await ta.fill(p);
  const btn = page.getByRole("button", { name: "Send", exact: true });
  const isVisible = await btn.isVisible().catch(() => false);
  log(`  R${i+1}: Send visible=${isVisible} → click`);
  if (isVisible) {
    await btn.click();
  } else {
    log(`  R${i+1}: Send NOT visible, skipping click`);
  }
  await page.waitForTimeout(500); // don't wait for reply, just fire next
}
// Now wait until Send comes back (all queued messages done)
try {
  await page.getByRole("button", { name: "Send", exact: true }).waitFor({ state: "visible", timeout: 180000 });
  log(`✅ rapid-fire settled`);
} catch { log(`❌ rapid-fire timed out at 180s`); }
await new Promise((r) => setTimeout(r, 5000));
await shot("mg-C-final");

const stateC = await fetchThreadState(tidB);
log(`state after rapid-fire: ${JSON.stringify(stateC)}`);

// Save events + summary
writeFileSync(join(runDir, "events.json"), JSON.stringify(events, null, 2));
writeFileSync(join(runDir, "poll.json"), JSON.stringify(pollLog, null, 2));
writeFileSync(join(runDir, "summary.json"), JSON.stringify({
  thread: tidB, devToolInfo,
  toolUse: { stateB, reviewerWindow: { firstMs: firstRev, lastMs: lastRev, durationMs: firstRev !== null ? lastRev - firstRev : null, firstPassMs: firstPass } },
  rapidFire: { stateC },
  counts: {
    consoleErrors: events.filter((e) => e.type === "console" && e.level === "error").length,
    pageErrors: events.filter((e) => e.type === "pageerror").length,
    badResponses: events.filter((e) => e.type === "badResp").length,
    requestFailures: events.filter((e) => e.type === "reqFail").length,
  },
}, null, 2));
log(`\nout: ${runDir}`);
await browser.close();
