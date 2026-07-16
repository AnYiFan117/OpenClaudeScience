// Iter 21: switch auth to Approve writes, then trigger a write-tool interrupt
import { chromium } from "playwright";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = "http://127.0.0.1:3000";
const MG_URL = `${BASE}/?assistantId=agent_local&resourceId=local&workspaceId=local-d15f5b3aa04d`;
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter21-${stamp}`);
mkdirSync(runDir, { recursive: true });

const events = [];
function log(m) { const l = `[${new Date().toISOString()}] ${m}`; console.log(l); appendFileSync(join(runDir, "run.log"), l + "\n"); }
function evt(t, d) { events.push({ t: new Date().toISOString(), type: t, ...d }); }

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
page.on("pageerror", (e) => evt("pageerror", { message: e.message }));
page.on("console", (m) => evt("console", { level: m.type(), text: m.text().slice(0, 400) }));
page.on("response", (r) => { if (r.status() >= 400) evt("badResp", { url: r.url(), status: r.status() }); });

let shotIdx = 0;
async function shot(name) {
  shotIdx += 1;
  await page.screenshot({ path: join(runDir, `${String(shotIdx).padStart(2, "0")}-${name}.png`) });
  log(`📸 ${String(shotIdx).padStart(2, "0")}-${name}`);
}

// ─── A) Set auth mode to Approve writes ─────────────────────────────────────
log(`\n=== A) set auth to Approve writes ===`);
await page.goto(`${BASE}/config#authorization`, { waitUntil: "domcontentloaded" });
await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
await page.waitForTimeout(2500);
// Scroll to Authorization section
await page.evaluate(() => {
  const h = Array.from(document.querySelectorAll("h1, h2, h3")).find((h) => (h.textContent || "").includes("Authorization"));
  h?.scrollIntoView({ behavior: "instant" });
});
await page.waitForTimeout(500);
await shot("A-auth-section");
// Click "Approve writes" radio
const approveWritesRadio = page.locator("button, div, label").filter({ hasText: /^Approve writes/ }).first();
if (await approveWritesRadio.isVisible().catch(() => false)) {
  await approveWritesRadio.click();
  await page.waitForTimeout(800);
  log(`  clicked Approve writes`);
  await shot("A-after-click");
} else {
  log(`❌ Approve writes not found`);
}

// ─── B) Trigger a write tool call ──────────────────────────────────────────
log(`\n=== B) trigger write in mg ===`);
await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);
const newBtn = page.getByRole("button", { name: "New", exact: true }).first();
if (await newBtn.isVisible().catch(() => false)) { await newBtn.click(); await page.waitForTimeout(1200); }
await shot("B-fresh");

const ta = page.locator("textarea").first();
await ta.click();
await ta.fill("请在 output/ 目录下写一个新文件 hello.txt，内容是 'hello from iter21'。");
await page.getByRole("button", { name: "Send", exact: true }).click();
log(`📤 sent write prompt`);

// Wait for either Send-visible (finished) or an approval dialog / interrupt to appear
const deadline = Date.now() + 90000;
let sawApproval = false;
while (Date.now() < deadline) {
  const state = await page.evaluate(() => {
    const bt = document.body.innerText;
    return {
      hasSend: Array.from(document.querySelectorAll("button")).some((b) => (b.textContent || "").trim() === "Send"),
      hasApprove: /Approve|批准|允许|Deny|拒绝/i.test(bt),
      hasStop: Array.from(document.querySelectorAll("button")).some((b) => (b.textContent || "").trim() === "Stop"),
      hasInterrupted: bt.includes("Interrupted"),
    };
  });
  if (state.hasApprove) { sawApproval = true; break; }
  if (state.hasSend && !state.hasStop) break;
  await page.waitForTimeout(500);
}
log(`  sawApproval=${sawApproval}`);
await shot("B-after-send");

// Find any approval dialog / interrupt card
const approvalCards = await page.evaluate(() => {
  return Array.from(document.querySelectorAll("[role='dialog'], [role='alertdialog'], div"))
    .filter((el) => {
      const txt = (el.textContent || "");
      return /Approve|批准|允许|Deny|拒绝/i.test(txt) && txt.length < 1500 && txt.length > 20;
    })
    .slice(0, 3)
    .map((el) => {
      const rect = el.getBoundingClientRect();
      const buttons = Array.from(el.querySelectorAll("button")).map((b) => (b.textContent || "").trim().slice(0, 40)).filter((t) => t);
      return { role: el.getAttribute("role"), text: (el.textContent || "").slice(0, 300), x: Math.round(rect.x), y: Math.round(rect.y), buttons: buttons.slice(0, 10) };
    });
});
log(`\napproval-like cards visible: ${approvalCards.length}`);
approvalCards.forEach((c, i) => {
  log(`  [${i}] role=${c.role} at (${c.x},${c.y})`);
  log(`      text="${c.text.slice(0, 200)}"`);
  log(`      buttons: ${JSON.stringify(c.buttons)}`);
});

// If approval card has buttons, click Approve/Allow/批准
if (approvalCards.length > 0) {
  log(`\n  attempting to click Approve/Allow`);
  const approveBtn = page.locator("button").filter({ hasText: /^(Approve|Allow|批准|允许)/ }).first();
  if (await approveBtn.isVisible().catch(() => false)) {
    await approveBtn.click();
    log(`  clicked approve`);
    await page.waitForTimeout(3000);
    await shot("B-after-approve");
  }
}

// Wait for stream to finish
try {
  await page.getByRole("button", { name: "Send", exact: true }).waitFor({ state: "visible", timeout: 60000 });
} catch { log(`  Send didn't come back in 60s`); }
await page.waitForTimeout(5000);
await shot("B-final");

// ─── C) Restore auth to Auto approve ───────────────────────────────────────
log(`\n=== C) restore Auto approve ===`);
await page.goto(`${BASE}/config#authorization`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(2500);
const autoBtn = page.locator("*").filter({ hasText: /^Auto approve/ }).first();
if (await autoBtn.isVisible().catch(() => false)) {
  await autoBtn.click();
  await page.waitForTimeout(500);
  log(`  restored Auto approve`);
  await shot("C-restored");
}

writeFileSync(join(runDir, "summary.json"), JSON.stringify({
  approvalCards, sawApproval,
  consoleErrors: events.filter((e) => e.type === "console" && e.level === "error").length,
  pageErrors: events.filter((e) => e.type === "pageerror").length,
}, null, 2));
log(`\nout: ${runDir}`);
await browser.close();
