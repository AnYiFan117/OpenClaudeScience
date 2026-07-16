// Iter 15: Files panel — click folders, filter, open file preview
import { chromium } from "playwright";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = "http://127.0.0.1:3000";
const MG_URL = `${BASE}/?assistantId=agent_local&resourceId=local&workspaceId=local-d15f5b3aa04d`;
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter15-${stamp}`);
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
  await page.screenshot({ path: join(runDir, `${String(shotIdx).padStart(2, "0")}-${name}.png`), fullPage: false });
  log(`📸 ${String(shotIdx).padStart(2, "0")}-${name}`);
}

log(`🚀 open mg`);
await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
await page.locator("textarea").first().waitFor({ state: "visible", timeout: 15000 });
await page.waitForTimeout(3000);
await shot("mg-loaded");

// ─── A) Explore folders in Files pane ──────────────────────────────────────
log(`\n=== A) folder tree ===`);
// Files panel folders should be right of x=850
async function filesPanelState() {
  return await page.evaluate(() => {
    const items = Array.from(document.querySelectorAll("button, a, div")).filter((el) => {
      const rect = el.getBoundingClientRect();
      return rect.x > 850 && rect.width > 30 && rect.height > 10 && rect.height < 60;
    }).map((el) => {
      const rect = el.getBoundingClientRect();
      return { text: (el.textContent || "").trim().slice(0, 40), x: Math.round(rect.x), y: Math.round(rect.y), tag: el.tagName.toLowerCase() };
    }).filter((it) => it.text.length > 0);
    // Dedup
    const seen = new Set();
    return items.filter((it) => { const k = `${it.text}@${it.y}`; if (seen.has(k)) return false; seen.add(k); return true; });
  });
}
const initial = await filesPanelState();
log(`initial file panel items: ${initial.length}`);
initial.slice(0, 15).forEach((it) => log(`  ${JSON.stringify(it)}`));

// Try expanding "output" folder (has the JSON files from earlier tool calls)
log(`\n👉 click "output" folder`);
const outputFolder = page.locator("button, a").filter({ hasText: /^output$/ }).first();
if (await outputFolder.isVisible().catch(() => false)) {
  await outputFolder.click();
  await page.waitForTimeout(1500);
  await shot("A-output-expanded");
} else {
  log(`❌ output folder not found`);
}
const afterExpand = await filesPanelState();
log(`after expand: ${afterExpand.length} items visible`);

// ─── B) Filter input ────────────────────────────────────────────────────────
log(`\n=== B) Filter input ===`);
const filterInput = page.locator("input[placeholder*='Filter'], input[placeholder*='过滤']").first();
if (await filterInput.isVisible().catch(() => false)) {
  await filterInput.click();
  await filterInput.fill("json");
  await page.waitForTimeout(800);
  await shot("B-filtered-json");
  const filtered = await filesPanelState();
  log(`filtered items (${filtered.length}):`);
  filtered.slice(0, 10).forEach((it) => log(`  ${JSON.stringify(it)}`));

  await filterInput.fill("xxxNOMATCHxxx");
  await page.waitForTimeout(800);
  await shot("B-filter-nomatch");
  const noMatch = await filesPanelState();
  log(`no-match items: ${noMatch.length}`);

  await filterInput.fill("");
  await page.waitForTimeout(500);
} else {
  log(`❌ filter input not found`);
}

// ─── C) Click on a file to preview ─────────────────────────────────────────
log(`\n=== C) file preview ===`);
// After clearing filter, try clicking on a specific JSON file
const jsonFile = page.locator("button, a, div").filter({ hasText: /pareto_data\.json|\.json$/ }).first();
if (await jsonFile.isVisible().catch(() => false)) {
  log(`  found JSON file, clicking`);
  await jsonFile.click();
  await page.waitForTimeout(2500);
  await shot("C-file-clicked");
  const previewState = await page.evaluate(() => {
    const dialogs = Array.from(document.querySelectorAll("[role='dialog']"));
    const hasFilePreview = dialogs.length > 0;
    const previewText = hasFilePreview ? (dialogs[0]?.textContent || "").slice(0, 200) : "";
    return { hasFilePreview, dialogCount: dialogs.length, previewText };
  });
  log(`  preview: ${JSON.stringify(previewState)}`);

  // Try to close
  const closeBtn = page.getByRole("button", { name: /close|关闭/i }).first();
  if (await closeBtn.isVisible().catch(() => false)) {
    await closeBtn.click();
    await page.waitForTimeout(500);
    await shot("C-preview-closed");
  } else {
    // Try Escape
    await page.keyboard.press("Escape");
    await page.waitForTimeout(500);
    await shot("C-preview-escaped");
  }
} else {
  log(`❌ no JSON file to click on`);
}

// ─── D) Test view mode toggle if present ───────────────────────────────────
log(`\n=== D) view mode toggle (grid vs list) ===`);
// Look for toggle buttons in the Files panel header
const viewToggle = await page.evaluate(() => {
  const btns = Array.from(document.querySelectorAll("button")).filter((b) => {
    const rect = b.getBoundingClientRect();
    return rect.x > 1250 && rect.y < 150 && rect.width < 50 && rect.height < 50;
  });
  return btns.map((b) => ({ hasIcon: !!b.querySelector("svg"), aria: b.getAttribute("aria-label"), title: b.getAttribute("title"), x: Math.round(b.getBoundingClientRect().x), y: Math.round(b.getBoundingClientRect().y) }));
});
log(`view toggle candidates: ${JSON.stringify(viewToggle)}`);

// Also, click the split-view / detached panel button if it exists (looks like grid at top-right)
if (viewToggle.length > 0) {
  const first = viewToggle[0];
  log(`click first toggle at (${first.x}, ${first.y})`);
  await page.mouse.click(first.x + 10, first.y + 10);
  await page.waitForTimeout(800);
  await shot("D-toggle-1");
  await page.mouse.click(first.x + 10, first.y + 10);
  await page.waitForTimeout(800);
  await shot("D-toggle-2");
}

writeFileSync(join(runDir, "summary.json"), JSON.stringify({
  events: events.length,
  consoleErrors: events.filter((e) => e.type === "console" && e.level === "error").length,
  pageErrors: events.filter((e) => e.type === "pageerror").length,
  badResponses: events.filter((e) => e.type === "badResp").length,
  errorList: events.filter((e) => e.type === "pageerror").map((e) => e.message),
}, null, 2));

log(`\nout: ${runDir}`);
await browser.close();
