import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = process.env.BASE_URL || "http://127.0.0.1:3000";
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });

const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const runDir = join(outDir, stamp);
mkdirSync(runDir, { recursive: true });

const consoleMsgs = [];
const pageErrors = [];
const badResponses = [];
const requestFailures = [];

console.log(`🚀 Launching Chromium, target=${BASE}`);
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();

page.on("console", (msg) => {
  consoleMsgs.push({
    type: msg.type(),
    text: msg.text(),
    location: msg.location(),
  });
});
page.on("pageerror", (err) => {
  pageErrors.push({ name: err.name, message: err.message, stack: err.stack });
});
page.on("response", (res) => {
  const status = res.status();
  if (status >= 400) {
    badResponses.push({ url: res.url(), status, method: res.request().method() });
  }
});
page.on("requestfailed", (req) => {
  requestFailures.push({ url: req.url(), method: req.method(), failure: req.failure()?.errorText });
});

const t0 = Date.now();
try {
  await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 30000 });
} catch (e) {
  console.log(`❌ goto failed: ${e.message}`);
  await browser.close();
  process.exit(1);
}
console.log(`✅ DOMContentLoaded in ${Date.now() - t0} ms`);

// Wait for the startup splash to be dismissed. The bootstrap script sets
// document.body.dataset.internagentsStartup = "ready" after DOMContentLoaded.
try {
  await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 });
  console.log(`✅ startup splash dismissed at ${Date.now() - t0} ms`);
} catch {
  console.log(`⚠️  startup splash still present after 20s — screenshotting anyway`);
}

// Give React/SWR a moment to hydrate and paint initial content.
await page.waitForTimeout(2000);

const title = await page.title();
console.log(`📄 title: ${title}`);

await page.screenshot({ path: join(runDir, "01-landing.png"), fullPage: true });
await page.screenshot({ path: join(runDir, "01-landing-viewport.png"), fullPage: false });
console.log(`📸 landing screenshots written`);

// Enumerate visible interactive elements — helps figure out what flows exist.
const interactive = await page.evaluate(() => {
  const items = [];
  const push = (el, kind) => {
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    if (rect.bottom < 0 || rect.top > window.innerHeight) return;
    const label =
      el.getAttribute("aria-label") ||
      el.getAttribute("title") ||
      el.getAttribute("placeholder") ||
      (el.textContent || "").trim().slice(0, 80);
    items.push({
      kind,
      tag: el.tagName.toLowerCase(),
      label,
      role: el.getAttribute("role"),
      testid: el.getAttribute("data-testid"),
      x: Math.round(rect.x),
      y: Math.round(rect.y),
    });
  };
  document.querySelectorAll("button, a[href], [role='button'], [role='tab'], [role='menuitem']").forEach((el) => push(el, "control"));
  document.querySelectorAll("input, textarea, [contenteditable='true']").forEach((el) => push(el, "input"));
  return items;
});
console.log(`🔎 interactive elements visible: ${interactive.length}`);

const report = {
  base: BASE,
  startedAt: new Date(t0).toISOString(),
  finishedAt: new Date().toISOString(),
  elapsedMs: Date.now() - t0,
  title,
  counts: {
    consoleMsgs: consoleMsgs.length,
    consoleErrors: consoleMsgs.filter((m) => m.type === "error").length,
    consoleWarnings: consoleMsgs.filter((m) => m.type === "warning").length,
    pageErrors: pageErrors.length,
    badResponses: badResponses.length,
    requestFailures: requestFailures.length,
    interactive: interactive.length,
  },
  pageErrors,
  consoleErrors: consoleMsgs.filter((m) => m.type === "error"),
  consoleWarnings: consoleMsgs.filter((m) => m.type === "warning"),
  badResponses,
  requestFailures,
  interactive,
};
writeFileSync(join(runDir, "report.json"), JSON.stringify(report, null, 2));
console.log(`📝 report written → ${join(runDir, "report.json")}`);

console.log(`\n=== SUMMARY ===`);
console.log(`title:            ${title}`);
console.log(`console errors:   ${report.counts.consoleErrors}`);
console.log(`console warnings: ${report.counts.consoleWarnings}`);
console.log(`page errors:      ${report.counts.pageErrors}`);
console.log(`bad responses:    ${report.counts.badResponses}`);
console.log(`request failures: ${report.counts.requestFailures}`);
console.log(`interactive els:  ${report.counts.interactive}`);
console.log(`out:              ${runDir}`);

await browser.close();
const hardFail = report.counts.pageErrors > 0 || report.counts.consoleErrors > 0 || report.counts.badResponses > 0 || report.counts.requestFailures > 0;
process.exit(hardFail ? 2 : 0);
