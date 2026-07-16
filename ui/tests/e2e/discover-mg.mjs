import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = process.env.BASE_URL || "http://127.0.0.1:3000";
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const runDir = join(outDir, `discover-mg-${stamp}`);
mkdirSync(runDir, { recursive: true });

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();

const consoleMsgs = [], pageErrors = [], badResp = [];
page.on("console", (m) => consoleMsgs.push({ type: m.type(), text: m.text() }));
page.on("pageerror", (e) => pageErrors.push({ message: e.message }));
page.on("response", (r) => { if (r.status() >= 400) badResp.push({ url: r.url(), status: r.status() }); });

console.log(`🚀 goto ${BASE}`);
await page.goto(BASE, { waitUntil: "domcontentloaded" });
await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
await page.waitForTimeout(1500);

async function dumpInteractive(label) {
  const items = await page.evaluate(() => {
    const out = [];
    const push = (el, kind) => {
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return;
      const label = el.getAttribute("aria-label") || el.getAttribute("title") || el.getAttribute("placeholder") || (el.textContent || "").trim().slice(0, 100);
      out.push({ kind, tag: el.tagName.toLowerCase(), label, cls: el.className?.toString().slice(0, 80), href: el.getAttribute("href"), x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) });
    };
    document.querySelectorAll("button, a[href], [role='button']").forEach((el) => push(el, "ctl"));
    document.querySelectorAll("input, textarea, [contenteditable='true']").forEach((el) => push(el, "in"));
    return out;
  });
  writeFileSync(join(runDir, `${label}.json`), JSON.stringify(items, null, 2));
  console.log(`  📋 ${label}: ${items.length} elements`);
  return items;
}

console.log(`📸 landing`);
await page.screenshot({ path: join(runDir, "01-landing.png"), fullPage: false });
const landing = await dumpInteractive("01-landing");

// Enter mg — click the "mg" project row's arrow (or the mg link itself).
console.log(`\n👉 click mg`);
const mgLink = landing.find((it) => it.tag === "a" && /^mg\b/.test(it.label));
if (!mgLink) {
  console.log(`❌ can't find mg link — landing dump saved`);
  await browser.close();
  process.exit(2);
}
console.log(`  found mg link at (${mgLink.x},${mgLink.y}) href=${mgLink.href}`);
if (mgLink.href) {
  await page.goto(new URL(mgLink.href, BASE).toString(), { waitUntil: "domcontentloaded" });
} else {
  await page.mouse.click(mgLink.x + 10, mgLink.y + 10);
}
await page.waitForTimeout(3000);

console.log(`📸 chat page`);
await page.screenshot({ path: join(runDir, "02-chat.png"), fullPage: false });
await page.screenshot({ path: join(runDir, "02-chat-full.png"), fullPage: true });
const chatItems = await dumpInteractive("02-chat");

// Highlight composer & send button in the report.
const composer = chatItems.find((it) => it.tag === "textarea");
const sendBtn = chatItems.find((it) => it.cls?.includes("ocs-composer-send"));
console.log(`\n🔎 composer:  ${composer ? `(${composer.x},${composer.y}) ${composer.w}x${composer.h} placeholder="${composer.label}"` : "NOT FOUND"}`);
console.log(`🔎 send btn:  ${sendBtn ? `(${sendBtn.x},${sendBtn.y}) label="${sendBtn.label}"` : "NOT FOUND"}`);

writeFileSync(join(runDir, "summary.json"), JSON.stringify({
  url: page.url(),
  composer, sendBtn,
  consoleErrors: consoleMsgs.filter((m) => m.type === "error"),
  pageErrors,
  badResp,
}, null, 2));

console.log(`\nout: ${runDir}`);
await browser.close();
