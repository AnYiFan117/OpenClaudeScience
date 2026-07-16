// Iter 18: Settings + Customize page audit
import { chromium } from "playwright";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = "http://127.0.0.1:3000";
const MG_URL = `${BASE}/?assistantId=agent_local&resourceId=local&workspaceId=local-d15f5b3aa04d`;
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter18-${stamp}`);
mkdirSync(runDir, { recursive: true });

const events = [];
function log(m) { const l = `[${new Date().toISOString()}] ${m}`; console.log(l); appendFileSync(join(runDir, "run.log"), l + "\n"); }
function evt(t, d) { events.push({ t: new Date().toISOString(), type: t, ...d }); }

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
page.on("pageerror", (e) => evt("pageerror", { message: e.message, stack: (e.stack || "").slice(0, 400) }));
page.on("console", (m) => evt("console", { level: m.type(), text: m.text().slice(0, 400) }));
page.on("response", (r) => { if (r.status() >= 400) evt("badResp", { url: r.url(), status: r.status() }); });

let shotIdx = 0;
async function shot(name) {
  shotIdx += 1;
  await page.screenshot({ path: join(runDir, `${String(shotIdx).padStart(2, "0")}-${name}.png`), fullPage: true });
  log(`📸 ${String(shotIdx).padStart(2, "0")}-${name}`);
}

async function pageAudit() {
  return await page.evaluate(() => {
    const url = window.location.href;
    const title = document.title;
    const h1s = Array.from(document.querySelectorAll("h1, h2, h3")).map((h) => (h.textContent || "").trim().slice(0, 80));
    const buttons = Array.from(document.querySelectorAll("button")).map((b) => (b.textContent || "").trim().slice(0, 50)).filter((t) => t.length > 0);
    const inputs = Array.from(document.querySelectorAll("input, textarea, select")).map((i) => ({ type: i.tagName.toLowerCase(), name: i.getAttribute("name"), placeholder: i.getAttribute("placeholder"), value: (i).value?.slice?.(0, 50) }));
    const links = Array.from(document.querySelectorAll("a[href]")).map((a) => ({ href: a.getAttribute("href"), text: (a.textContent || "").trim().slice(0, 40) })).filter((l) => l.text);
    return { url, title, h1s, buttons: [...new Set(buttons)].slice(0, 30), inputs: inputs.slice(0, 20), links: links.slice(0, 20) };
  });
}

log(`🚀 open mg`);
await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
await page.locator("textarea").first().waitFor({ state: "visible", timeout: 15000 });
await page.waitForTimeout(2500);

// ─── A) Click Settings ─────────────────────────────────────────────────────
log(`\n=== A) Settings ===`);
const settings = page.getByRole("link", { name: "Settings", exact: true }).first().or(page.getByRole("button", { name: "Settings", exact: true }).first());
await settings.click({ timeout: 5000 }).catch(() => log(`  Settings click failed`));
await page.waitForTimeout(2000);
await shot("A-settings");
const settingsAudit = await pageAudit();
log(`  Settings URL: ${settingsAudit.url}`);
log(`  headings: ${JSON.stringify(settingsAudit.h1s)}`);
log(`  ${settingsAudit.buttons.length} unique buttons, ${settingsAudit.inputs.length} inputs`);
log(`  buttons: ${JSON.stringify(settingsAudit.buttons.slice(0, 15))}`);
log(`  inputs: ${JSON.stringify(settingsAudit.inputs.slice(0, 5))}`);

// Try scrolling to see more
await page.evaluate(() => window.scrollTo({ top: document.body.scrollHeight, behavior: "instant" }));
await page.waitForTimeout(500);
await shot("A-settings-bottom");

// Navigate back to workbench
await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(2500);

// ─── B) Click Customize ────────────────────────────────────────────────────
log(`\n=== B) Customize ===`);
const customize = page.getByRole("link", { name: "Customize", exact: true }).first().or(page.getByRole("button", { name: "Customize", exact: true }).first());
await customize.click({ timeout: 5000 }).catch(() => log(`  Customize click failed`));
await page.waitForTimeout(2000);
await shot("B-customize");
const customizeAudit = await pageAudit();
log(`  Customize URL: ${customizeAudit.url}`);
log(`  headings: ${JSON.stringify(customizeAudit.h1s)}`);
log(`  buttons (${customizeAudit.buttons.length}): ${JSON.stringify(customizeAudit.buttons.slice(0, 15))}`);
log(`  inputs (${customizeAudit.inputs.length}): ${JSON.stringify(customizeAudit.inputs.slice(0, 10))}`);

// Try editing an input if any
if (customizeAudit.inputs.length > 0) {
  log(`\n  trying to interact with first input`);
  const firstInput = page.locator("input, textarea").first();
  if (await firstInput.isVisible().catch(() => false)) {
    const before = await firstInput.inputValue();
    await firstInput.click();
    await firstInput.fill("test-value-from-iter18");
    await page.waitForTimeout(400);
    await shot("B-input-edited");
    log(`  input before="${before.slice(0, 30)}" set to test-value`);
    // Restore
    await firstInput.fill(before);
  }
}

// ─── C) Console/page errors on these pages? ────────────────────────────────
log(`\n=== errors seen ===`);
log(`  console errors: ${events.filter((e) => e.type === "console" && e.level === "error").length}`);
log(`  page errors: ${events.filter((e) => e.type === "pageerror").length}`);
log(`  bad responses: ${events.filter((e) => e.type === "badResp").length}`);
for (const e of events.filter((x) => x.type === "pageerror").slice(0, 5)) log(`    PE: ${e.message.slice(0, 200)}`);
for (const e of events.filter((x) => x.type === "badResp").slice(0, 5)) log(`    ${e.status}: ${e.url.slice(0, 100)}`);

writeFileSync(join(runDir, "summary.json"), JSON.stringify({
  settings: settingsAudit,
  customize: customizeAudit,
  events: events.length,
  errors: events.filter((e) => e.type === "pageerror" || (e.type === "console" && e.level === "error") || e.type === "badResp"),
}, null, 2));
log(`\nout: ${runDir}`);
await browser.close();
