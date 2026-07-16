import { chromium } from "playwright";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = process.env.BASE_URL || "http://127.0.0.1:3000";
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter1-onboard-${stamp}`);
mkdirSync(runDir, { recursive: true });

const wsSlug = `e2e_iter1_${Date.now()}`;
const wsPath = `/tmp/${wsSlug}`;
mkdirSync(wsPath, { recursive: true });

const events = [];
const logPath = join(runDir, "run.log");
function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}`;
  console.log(line);
  appendFileSync(logPath, line + "\n");
}
function evt(type, detail) { events.push({ t: new Date().toISOString(), type, ...detail }); }

log(`🆕 workspace path: ${wsPath}`);

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();

page.on("console", (m) => evt("console", { level: m.type(), text: m.text().slice(0, 500) }));
page.on("pageerror", (e) => evt("pageerror", { message: e.message }));
page.on("response", (r) => { if (r.status() >= 400) evt("badResp", { url: r.url(), status: r.status(), method: r.request().method() }); });
page.on("requestfailed", (r) => evt("reqFail", { url: r.url(), method: r.method(), reason: r.failure()?.errorText }));

// Auto-fill the manual-path prompt with our test path.
page.on("dialog", async (d) => {
  log(`🗨️  dialog(${d.type()}): ${d.message().slice(0, 100)}`);
  if (d.type() === "prompt") await d.accept(wsPath);
  else await d.accept();
});

let shotIdx = 0;
async function shot(name) {
  shotIdx += 1;
  const label = `${String(shotIdx).padStart(2, "0")}-${name}`;
  await page.screenshot({ path: join(runDir, `${label}.png`), fullPage: false });
  log(`📸 ${label}`);
}

// ─── A) Onboarding: create workspace via 'New project' ─────────────────────
log(`\n=== A) ONBOARDING: create new workspace ===`);
log(`🚀 goto ${BASE}`);
await page.goto(BASE, { waitUntil: "domcontentloaded" });
await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
await page.waitForTimeout(1000);
await shot("landing");

// Count existing workspaces so we can verify a new one was added.
const beforeWorkspaceLinks = await page.locator("a").filter({ hasText: /\/mnt\/|\/tmp\// }).count();
log(`ℹ️  workspaces before: ${beforeWorkspaceLinks}`);

const newProjBtn = page.getByRole("button", { name: "New project", exact: true });
log(`👉 clicking New project`);
const t0Onboard = Date.now();
await newProjBtn.click();
// Wait for redirect (workbench url) — pickWorkspace pushes to workbench href on success.
try {
  await page.waitForURL(/workspaceId=/, { timeout: 20000 });
  log(`✅ redirected to workbench in ${Date.now() - t0Onboard} ms → ${page.url()}`);
} catch (e) {
  log(`❌ never redirected: ${e.message}`);
  await shot("onboard-failed");
  writeFileSync(join(runDir, "events.json"), JSON.stringify(events, null, 2));
  await browser.close();
  process.exit(2);
}
await page.locator("textarea").first().waitFor({ state: "visible", timeout: 15000 });
await page.waitForTimeout(1500);
await shot("onboarded-chat");

// Sanity: is the sidebar showing our new project?
const sideText = await page.locator("body").innerText();
const workspaceVisible = sideText.includes(wsSlug);
log(`ℹ️  new workspace slug visible on page: ${workspaceVisible}`);

// ─── B) Send science prompt + fast-poll reviewer ──────────────────────────
log(`\n=== B) science prompt + fast-poll reviewer ===`);
const ta = page.locator("textarea").first();
await ta.click();
const prompt = "请用两三句话解释：为什么大质量恒星的演化终点通常是超新星爆发？";
await ta.fill(prompt);

// Start high-frequency polling in a Node timer for reviewer state.
const pollLog = [];
const REVIEW_RE = /reviewing…|reviewing\.\.\.|正在审查中/;
let stopPoll = false;
const pollStarted = Date.now();
const pollTask = (async () => {
  while (!stopPoll) {
    const t = Date.now() - pollStarted;
    try {
      const info = await page.evaluate((re) => {
        const bodyText = document.body.innerText;
        const hit = new RegExp(re).exec(bodyText);
        // Any button in Stop state?
        const stopBtn = Array.from(document.querySelectorAll("button")).some((b) => (b.textContent || "").trim() === "Stop");
        const sendBtn = Array.from(document.querySelectorAll("button")).some((b) => (b.textContent || "").trim() === "Send");
        return { reviewingMatch: hit?.[0] ?? null, stopBtn, sendBtn };
      }, REVIEW_RE.source);
      pollLog.push({ t, ...info });
    } catch (_) {
      // Page might have re-rendered; ignore transient
    }
    await new Promise((r) => setTimeout(r, 100));
  }
})();

log(`📤 send prompt`);
await page.getByRole("button", { name: "Send", exact: true }).click();

// Wait for Send button to come back (assistant streaming done)
try {
  await page.getByRole("button", { name: "Send", exact: true }).waitFor({ state: "visible", timeout: 90000 });
  log(`✅ Send button returned at ${Date.now() - pollStarted} ms since poll start`);
} catch {
  log(`❌ Send button never returned within 90s`);
}
await shot("science-response");

// Continue polling briefly to catch reviewer completion.
await new Promise((r) => setTimeout(r, 8000));
stopPoll = true;
await pollTask;
await shot("science-final");

// Analyze poll log for reviewer window.
let firstReviewingAt = null, lastReviewingAt = null;
for (const p of pollLog) {
  if (p.reviewingMatch) {
    if (firstReviewingAt === null) firstReviewingAt = p.t;
    lastReviewingAt = p.t;
  }
}
const reviewingDuration = (firstReviewingAt !== null && lastReviewingAt !== null) ? lastReviewingAt - firstReviewingAt : null;
log(`\n🔎 REVIEWER TIMING:`);
log(`  first "reviewing…" seen at: ${firstReviewingAt} ms since poll start`);
log(`  last  "reviewing…" seen at: ${lastReviewingAt} ms`);
log(`  reviewer visible window:    ${reviewingDuration} ms`);

// Any card state at end?
const endState = await page.evaluate(() => {
  const pills = Array.from(document.querySelectorAll("*")).filter((el) => {
    const txt = (el.textContent || "").trim();
    return /^(review|reviewing…|review pass|review fail|review warn)$/i.test(txt) && el.children.length === 0;
  }).map((el) => (el.textContent || "").trim());
  const cards = Array.from(document.querySelectorAll("*")).map((el) => (el.textContent || "").slice(0, 40)).filter((t) => /pass|fail|warn|reviewing/i.test(t)).slice(0, 5);
  return { pills: pills.slice(0, 10), sample: cards };
});
log(`endState: ${JSON.stringify(endState)}`);

// ─── Report ────────────────────────────────────────────────────────────────
writeFileSync(join(runDir, "events.json"), JSON.stringify(events, null, 2));
writeFileSync(join(runDir, "poll.json"), JSON.stringify(pollLog, null, 2));
writeFileSync(join(runDir, "summary.json"), JSON.stringify({
  workspacePath: wsPath,
  workspaceVisibleInSidebar: workspaceVisible,
  onboardTimeMs: Date.now() - t0Onboard,
  reviewerTiming: { firstReviewingAt, lastReviewingAt, durationMs: reviewingDuration },
  counts: {
    consoleErrors: events.filter((e) => e.type === "console" && e.level === "error").length,
    consoleWarnings: events.filter((e) => e.type === "console" && e.level === "warning").length,
    pageErrors: events.filter((e) => e.type === "pageerror").length,
    badResponses: events.filter((e) => e.type === "badResp").length,
    requestFailures: events.filter((e) => e.type === "reqFail").length,
  },
  pollSamples: pollLog.length,
  endState,
}, null, 2));

log(`\n=== SUMMARY ===`);
log(`workspace: ${wsPath}`);
log(`onboarded in: ${Date.now() - t0Onboard} ms`);
log(`reviewer visible: ${reviewingDuration} ms`);
log(`console errors: ${events.filter((e) => e.type === "console" && e.level === "error").length}`);
log(`out: ${runDir}`);

await browser.close();
