// Iter 23: verify Apply persists to config, then test if backend uses it
import { chromium } from "playwright";
import { readFileSync } from "node:fs";
import { execSync } from "node:child_process";

const BASE = "http://127.0.0.1:3000";
const MG_URL = `${BASE}/?assistantId=agent_local&resourceId=local&workspaceId=local-d15f5b3aa04d`;

function log(m) { console.log(`[${new Date().toISOString()}] ${m}`); }

async function readCfg() {
  const raw = readFileSync("/mnt/shared-storage-user/wangrunsheng-p/OpenClaudeScience/deepagent.config.json", "utf-8");
  const c = JSON.parse(raw);
  return { authorization_mode: c.authorization_mode, interrupt_on_keys: Object.keys(c.interrupt_on || {}) };
}

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
page.on("pageerror", (e) => log(`  PE: ${e.message.slice(0, 200)}`));

log(`initial config: ${JSON.stringify(await readCfg())}`);

// A) Set Approve writes + apply
log(`\n=== A) apply Approve writes ===`);
await page.goto(`${BASE}/config`, { waitUntil: "domcontentloaded" });
await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
await page.waitForTimeout(2500);
await page.evaluate(() => {
  const h = Array.from(document.querySelectorAll("h1, h2, h3, span")).find((h) => (h.textContent || "").trim() === "Authorization");
  h?.scrollIntoView({ behavior: "instant" });
});
await page.waitForTimeout(500);
await page.locator("*").filter({ hasText: /^Approve writes/ }).first().click();
await page.waitForTimeout(500);
await page.getByRole("button", { name: /Save and apply now/i }).first().click();
await page.waitForTimeout(2500);
log(`after Save and apply now: ${JSON.stringify(await readCfg())}`);

// B) Trigger a write in mg and monitor
log(`\n=== B) send write prompt ===`);
await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);
await page.getByRole("button", { name: "New", exact: true }).first().click().catch(() => {});
await page.waitForTimeout(1500);
const ta = page.locator("textarea").first();
await ta.click();
const stamp = Date.now();
await ta.fill(`请在 output/ 目录下新建一个文件 iter23_${stamp}.txt，内容是 'iter23'。`);
log(`config right before send: ${JSON.stringify(await readCfg())}`);

const tSend = Date.now();
await page.getByRole("button", { name: "Send", exact: true }).click();

// Poll for approval / completion
const deadline = Date.now() + 120000;
let observed = null;
while (Date.now() < deadline) {
  observed = await page.evaluate(() => {
    const btns = Array.from(document.querySelectorAll("button")).map((b) => (b.textContent || "").trim());
    const hasApprove = btns.some((t) => /^Approve\b|^Allow|^批准|^全部批准/.test(t));
    const hasSend = btns.some((t) => t === "Send");
    const hasStop = btns.some((t) => t === "Stop");
    return { hasApprove, hasSend, hasStop, matchedButtons: btns.filter((t) => /Approve|Deny|Reject|批准|拒绝/i.test(t)) };
  });
  if (observed.hasApprove) break;
  if (observed.hasSend && !observed.hasStop) break;
  await new Promise((r) => setTimeout(r, 500));
}
log(`elapsed ${Date.now() - tSend}ms, observed: ${JSON.stringify(observed)}`);
await page.screenshot({ path: "/tmp/iter23-after-send.png" });

// C) Restore to Auto approve
log(`\n=== C) restore ===`);
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
  await page.waitForTimeout(2000);
}
log(`final config: ${JSON.stringify(await readCfg())}`);

await browser.close();
