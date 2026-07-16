// Iter 22: proper apply of Auth mode + trigger tool approval
import { chromium } from "playwright";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = "http://127.0.0.1:3000";
const MG_URL = `${BASE}/?assistantId=agent_local&resourceId=local&workspaceId=local-d15f5b3aa04d`;
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter22-${stamp}`);
mkdirSync(runDir, { recursive: true });

const events = [];
function log(m) { const l = `[${new Date().toISOString()}] ${m}`; console.log(l); appendFileSync(join(runDir, "run.log"), l + "\n"); }
function evt(t, d) { events.push({ t: new Date().toISOString(), type: t, ...d }); }

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
page.on("pageerror", (e) => evt("pageerror", { message: e.message }));
page.on("console", (m) => evt("console", { level: m.type(), text: m.text().slice(0, 400) }));

let shotIdx = 0;
async function shot(name) {
  shotIdx += 1;
  await page.screenshot({ path: join(runDir, `${String(shotIdx).padStart(2, "0")}-${name}.png`) });
  log(`📸 ${String(shotIdx).padStart(2, "0")}-${name}`);
}

// ─── A) change auth → click Approve writes → Apply now ─────────────────────
log(`\n=== A) select Approve writes + click Apply now ===`);
await page.goto(`${BASE}/config`, { waitUntil: "domcontentloaded" });
await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
await page.waitForTimeout(2500);
// Scroll to Authorization
await page.evaluate(() => {
  const h = Array.from(document.querySelectorAll("h1, h2, h3, span")).find((h) => (h.textContent || "").trim() === "Authorization");
  h?.scrollIntoView({ behavior: "instant" });
});
await page.waitForTimeout(500);
await shot("A-auth-before");

// Click Approve writes card
await page.locator("*").filter({ hasText: /^Approve writes/ }).first().click();
await page.waitForTimeout(600);
await shot("A-approve-writes-selected");

// Click "Save and apply now" at top of Settings
const applyNow = page.getByRole("button", { name: /Save and apply now/i }).first();
if (await applyNow.isVisible().catch(() => false)) {
  await applyNow.click();
  log(`  clicked Save and apply now`);
  await page.waitForTimeout(2500);
  await shot("A-applied");
} else {
  log(`❌ Save and apply now button not visible`);
}

// ─── B) go to mg, send write prompt ─────────────────────────────────────────
log(`\n=== B) trigger write in mg ===`);
await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);
const newBtn = page.getByRole("button", { name: "New", exact: true }).first();
if (await newBtn.isVisible().catch(() => false)) { await newBtn.click(); await page.waitForTimeout(1500); }
await shot("B-fresh-session");

const ta = page.locator("textarea").first();
await ta.click();
await ta.fill(`请在 output/ 目录下新建一个文件 iter22_fresh_${Date.now()}.txt，内容是 'unique write ${Date.now()}'。`);
await page.getByRole("button", { name: "Send", exact: true }).click();
log(`📤 sent write prompt`);

// Poll for approval UI or completion for up to 120s
const deadline = Date.now() + 120000;
let approvalObserved = false;
let approvalCard = null;
while (Date.now() < deadline) {
  approvalCard = await page.evaluate(() => {
    // Look for a distinctive tool-approval card element
    // ToolApprovalInterrupt renders a div with buttons: Approve/Deny/Edit/Skip
    const candidates = Array.from(document.querySelectorAll("div")).filter((el) => {
      const btns = Array.from(el.querySelectorAll(":scope > * button, :scope > button, :scope button")).slice(0, 8);
      const labels = btns.map((b) => (b.textContent || "").trim());
      return labels.some((l) => /^(Approve|Approve all|Deny|Skip|批准|拒绝|全部批准|Reject|Allow|Edit)/i.test(l));
    });
    if (candidates.length === 0) return null;
    const el = candidates[0];
    const rect = el.getBoundingClientRect();
    const buttons = Array.from(el.querySelectorAll("button")).map((b) => (b.textContent || "").trim()).filter((t) => t.length > 0);
    return {
      text: (el.textContent || "").slice(0, 400),
      buttons: [...new Set(buttons)].slice(0, 8),
      x: Math.round(rect.x), y: Math.round(rect.y),
      w: Math.round(rect.width), h: Math.round(rect.height),
    };
  });
  if (approvalCard) { approvalObserved = true; break; }
  await page.waitForTimeout(500);
}
log(`  approvalObserved=${approvalObserved}`);
if (approvalCard) {
  log(`    text=${JSON.stringify(approvalCard.text.slice(0, 200))}`);
  log(`    buttons=${JSON.stringify(approvalCard.buttons)}`);
  log(`    rect=(${approvalCard.x},${approvalCard.y}) ${approvalCard.w}x${approvalCard.h}`);
}
await shot("B-after-send");

// If we see the approval card, screenshot and click Approve
if (approvalCard) {
  await shot("B-approval-card-visible");
  const approveBtn = page.locator("button").filter({ hasText: /^(Approve|Allow|批准)$/ }).first();
  if (await approveBtn.isVisible().catch(() => false)) {
    log(`  clicking Approve`);
    await approveBtn.click();
    await page.waitForTimeout(3000);
    await shot("B-after-approve");
  }
}

// Wait for Send button return
try {
  await page.getByRole("button", { name: "Send", exact: true }).waitFor({ state: "visible", timeout: 60000 });
  log(`  Send button back`);
} catch { log(`  Send didn't come back`); }
await page.waitForTimeout(4000);
await shot("B-final");

// Check whether file actually got written (backend side effect)
const fileR = await fetch(`${BASE}/api/workspaces`);
log(`  workspace check status: ${fileR.status}`);

// ─── C) restore Auto approve ───────────────────────────────────────────────
log(`\n=== C) restore Auto approve ===`);
await page.goto(`${BASE}/config`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(2500);
await page.evaluate(() => {
  const h = Array.from(document.querySelectorAll("h1, h2, h3, span")).find((h) => (h.textContent || "").trim() === "Authorization");
  h?.scrollIntoView({ behavior: "instant" });
});
await page.waitForTimeout(500);
await page.locator("*").filter({ hasText: /^Auto approve/ }).first().click();
await page.waitForTimeout(500);
const applyRestore = page.getByRole("button", { name: /Save and apply now/i }).first();
if (await applyRestore.isVisible().catch(() => false)) {
  await applyRestore.click();
  log(`  restored + Applied Auto approve`);
}
await page.waitForTimeout(2000);
await shot("C-restored");

writeFileSync(join(runDir, "summary.json"), JSON.stringify({
  approvalObserved, approvalCard,
  consoleErrors: events.filter((e) => e.type === "console" && e.level === "error").length,
  pageErrors: events.filter((e) => e.type === "pageerror").length,
}, null, 2));
log(`\nout: ${runDir}`);
await browser.close();
