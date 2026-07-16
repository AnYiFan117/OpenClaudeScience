// Iter 17: composer keyboard shortcuts — @, #, /, Ctrl+K
import { chromium } from "playwright";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = "http://127.0.0.1:3000";
const MG_URL = `${BASE}/?assistantId=agent_local&resourceId=local&workspaceId=local-d15f5b3aa04d`;
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter17-${stamp}`);
mkdirSync(runDir, { recursive: true });

const events = [];
function log(m) { const l = `[${new Date().toISOString()}] ${m}`; console.log(l); appendFileSync(join(runDir, "run.log"), l + "\n"); }
function evt(t, d) { events.push({ t: new Date().toISOString(), type: t, ...d }); }

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
page.on("pageerror", (e) => evt("pageerror", { message: e.message, stack: (e.stack || "").slice(0, 300) }));
page.on("console", (m) => evt("console", { level: m.type(), text: m.text().slice(0, 400) }));

let shotIdx = 0;
async function shot(name) {
  shotIdx += 1;
  await page.screenshot({ path: join(runDir, `${String(shotIdx).padStart(2, "0")}-${name}.png`) });
  log(`📸 ${String(shotIdx).padStart(2, "0")}-${name}`);
}

async function popoverSnapshot() {
  return await page.evaluate(() => {
    const popovers = Array.from(document.querySelectorAll("[role='listbox'], [role='menu'], [role='dialog']")).filter((el) => {
      const rect = el.getBoundingClientRect();
      return rect.width > 100 && rect.height > 30;
    });
    return popovers.map((p) => {
      const rect = p.getBoundingClientRect();
      const items = Array.from(p.querySelectorAll("[role='option'], [role='menuitem'], button")).slice(0, 8).map((el) => (el.textContent || "").trim().slice(0, 60));
      return { role: p.getAttribute("role"), x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height), items };
    });
  });
}

log(`🚀 open mg + new session`);
await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
await page.locator("textarea").first().waitFor({ state: "visible", timeout: 15000 });
await page.waitForTimeout(2500);
const newBtn = page.getByRole("button", { name: "New", exact: true }).first();
if (await newBtn.isVisible().catch(() => false)) { await newBtn.click(); await page.waitForTimeout(1200); }

const ta = page.locator("textarea").first();

async function testKey(key, tag, closeKey = "Escape") {
  log(`\n=== ${tag}: type "${key}" ===`);
  await ta.click();
  await ta.fill("");
  await page.keyboard.type(key);
  await page.waitForTimeout(500);
  await shot(`${tag}-opened`);
  const popovers = await popoverSnapshot();
  log(`  popovers: ${popovers.length}`);
  for (const p of popovers) {
    log(`    role=${p.role} at (${p.x},${p.y}) ${p.w}x${p.h} items=${JSON.stringify(p.items.slice(0, 4))}`);
  }
  // Type a filter char
  await page.keyboard.type("s");
  await page.waitForTimeout(400);
  await shot(`${tag}-filtered`);
  const filtered = await popoverSnapshot();
  log(`  after 's' filter: ${filtered.length} popovers`);
  for (const p of filtered) log(`    items=${JSON.stringify(p.items.slice(0, 4))}`);
  // Close
  await page.keyboard.press(closeKey);
  await page.waitForTimeout(400);
  await shot(`${tag}-closed`);
  const closed = await popoverSnapshot();
  log(`  after ${closeKey}: ${closed.length} popovers (0 = closed correctly)`);
  await ta.fill("");
}

// A) @ artifacts
await testKey("@", "A-at");
// B) # sessions
await testKey("#", "B-hash");
// C) / skills
await testKey("/", "C-slash");
// D) Ctrl+K search
log(`\n=== D: Ctrl+K global search ===`);
await ta.click();
await ta.fill("");
await page.keyboard.press("Control+k");
await page.waitForTimeout(500);
await shot("D-ctrlk-opened");
const d = await popoverSnapshot();
log(`  popovers after Ctrl+K: ${d.length}`);
d.forEach((p) => log(`    role=${p.role} items=${JSON.stringify(p.items.slice(0, 4))}`));
await page.keyboard.press("Escape");
await page.waitForTimeout(400);

// E) empty send guard — type just @ / #, no filter, hit Enter (should not send)
log(`\n=== E: pressing Enter with just '@' — should not send ===`);
await ta.click();
await ta.fill("");
await page.keyboard.type("@");
await page.waitForTimeout(300);
await page.keyboard.press("Enter");
await page.waitForTimeout(500);
await shot("E-at-enter");
// The @ picker should have handled Enter (select first item or dismiss)

writeFileSync(join(runDir, "summary.json"), JSON.stringify({
  events: events.length,
  consoleErrors: events.filter((e) => e.type === "console" && e.level === "error").length,
  pageErrors: events.filter((e) => e.type === "pageerror").length,
  pageErrorList: events.filter((e) => e.type === "pageerror").map((e) => e.message),
}, null, 2));

log(`\nout: ${runDir}`);
await browser.close();
