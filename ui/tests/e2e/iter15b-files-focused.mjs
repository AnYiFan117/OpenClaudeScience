// Iter 15b: focused test — expand output folder, click JSON, verify preview
import { chromium } from "playwright";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = "http://127.0.0.1:3000";
const MG_URL = `${BASE}/?assistantId=agent_local&resourceId=local&workspaceId=local-d15f5b3aa04d`;
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter15b-${stamp}`);
mkdirSync(runDir, { recursive: true });

const events = [];
function log(m) { const l = `[${new Date().toISOString()}] ${m}`; console.log(l); appendFileSync(join(runDir, "run.log"), l + "\n"); }
function evt(t, d) { events.push({ t: new Date().toISOString(), type: t, ...d }); }

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
page.on("console", (m) => evt("console", { level: m.type(), text: m.text().slice(0, 400) }));
page.on("pageerror", (e) => evt("pageerror", { message: e.message, stack: (e.stack || "").slice(0, 300) }));
page.on("response", (r) => { if (r.status() >= 400) evt("badResp", { url: r.url(), status: r.status() }); });

let shotIdx = 0;
async function shot(name) {
  shotIdx += 1;
  await page.screenshot({ path: join(runDir, `${String(shotIdx).padStart(2, "0")}-${name}.png`) });
  log(`📸 ${String(shotIdx).padStart(2, "0")}-${name}`);
}

log(`🚀 open mg`);
await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
await page.locator("textarea").first().waitFor({ state: "visible", timeout: 15000 });
await page.waitForTimeout(3000);
await shot("loaded");

// ─── A) click output folder ────────────────────────────────────────────────
log(`\n=== A) expand output folder ===`);
const outputRow = page.locator("[role='button'], button").filter({ hasText: /^output/ }).filter({ hasNotText: /检查一下|列出|文件/ }).first();
if (!(await outputRow.isVisible().catch(() => false))) {
  log(`❌ output row not found — trying div locator`);
  // Alternative: find via specific CSS classes or DOM traversal
  await page.locator("text=/^output$/").first().click({ force: true }).catch((e) => log(`  force click failed: ${e.message}`));
} else {
  await outputRow.click();
}
await page.waitForTimeout(1500);
await shot("A-output-expanded");

// ─── B) click a JSON file inside ──────────────────────────────────────────
log(`\n=== B) click a JSON file ===`);
const jsonEntry = page.locator("[role='button'], button, div").filter({ hasText: /pareto_data\.json/ }).first();
if (await jsonEntry.isVisible().catch(() => false)) {
  await jsonEntry.click();
  log(`  clicked pareto_data.json`);
} else {
  // Fallback: click any .json
  const anyJson = page.locator("*").filter({ hasText: /\.json$/ }).first();
  if (await anyJson.isVisible().catch(() => false)) {
    await anyJson.click({ force: true });
    log(`  clicked first .json entry`);
  } else {
    log(`❌ no json file visible`);
  }
}
await page.waitForTimeout(2000);
await shot("B-file-clicked");

// Check for preview dialog
const dialogState = await page.evaluate(() => {
  const dialogs = Array.from(document.querySelectorAll("[role='dialog']"));
  return dialogs.map((d) => {
    const rect = d.getBoundingClientRect();
    return { text: (d.textContent || "").slice(0, 300), w: Math.round(rect.width), h: Math.round(rect.height) };
  });
});
log(`  dialogs on page: ${dialogState.length}`);
for (const d of dialogState) log(`    ${JSON.stringify(d)}`);

// ─── C) Try close preview via Escape ───────────────────────────────────────
if (dialogState.length > 0) {
  await page.keyboard.press("Escape");
  await page.waitForTimeout(500);
  await shot("C-escape");
  const afterEsc = await page.locator("[role='dialog']").count();
  log(`  after Escape: dialogs=${afterEsc}`);
}

// ─── D) Filter test ────────────────────────────────────────────────────────
log(`\n=== D) filter input ===`);
const filter = page.locator("input[placeholder*='Filter']").first();
if (await filter.isVisible().catch(() => false)) {
  await filter.fill("json");
  await page.waitForTimeout(800);
  await shot("D-filter-json");
  const visible = await page.evaluate(() => {
    return Array.from(document.querySelectorAll("*")).filter((el) => {
      const rect = el.getBoundingClientRect();
      const t = (el.textContent || "").trim();
      return rect.x > 850 && rect.y > 100 && rect.y < 700 && t.length > 3 && t.length < 40 && !t.includes("\n");
    }).map((el) => (el.textContent || "").trim().slice(0, 40));
  });
  const uniq = [...new Set(visible)].slice(0, 15);
  log(`  visible (with 'json' filter):`);
  uniq.forEach((v) => log(`    ${v}`));
  await filter.fill("");
}

writeFileSync(join(runDir, "summary.json"), JSON.stringify({
  events: events.length,
  consoleErrors: events.filter((e) => e.type === "console" && e.level === "error").length,
  pageErrors: events.filter((e) => e.type === "pageerror").length,
  errorList: events.filter((e) => e.type === "pageerror").map((e) => e.message),
}, null, 2));

log(`\nout: ${runDir}`);
await browser.close();
