// Iter 19: composer input stress
import { chromium } from "playwright";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = "http://127.0.0.1:3000";
const MG_URL = `${BASE}/?assistantId=agent_local&resourceId=local&workspaceId=local-d15f5b3aa04d`;
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter19-${stamp}`);
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

log(`🚀 open mg + new session`);
await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
await page.locator("textarea").first().waitFor({ state: "visible", timeout: 15000 });
await page.waitForTimeout(2500);
const newBtn = page.getByRole("button", { name: "New", exact: true }).first();
if (await newBtn.isVisible().catch(() => false)) { await newBtn.click(); await page.waitForTimeout(1200); }

const ta = page.locator("textarea").first();

// ─── A) Paste 100k chars ───────────────────────────────────────────────────
log(`\n=== A) 100k char paste ===`);
const bigText = "科研数据点。".repeat(20000); // ~120k chars
const tFill = Date.now();
await ta.click();
await ta.fill(bigText);
log(`  fill(${bigText.length} chars) took ${Date.now() - tFill} ms`);
await page.waitForTimeout(500);
await shot("A-100k-filled");
const state1 = await page.evaluate(() => ({
  taValue: (document.querySelector("textarea"))?.value?.length,
  bodyHeight: document.body.scrollHeight,
  memMB: (performance).memory ? Math.round((performance).memory.usedJSHeapSize / 1024 / 1024) : null,
}));
log(`  state: ${JSON.stringify(state1)}`);
await ta.fill(""); // clear

// ─── B) Rapid Enter spam ───────────────────────────────────────────────────
log(`\n=== B) Enter spam (10 rapid) ===`);
await ta.click();
await ta.fill("Rapid enter test");
const tEnter = Date.now();
for (let i = 0; i < 10; i++) {
  await page.keyboard.press("Enter");
}
log(`  10x Enter took ${Date.now() - tEnter} ms`);
await page.waitForTimeout(2000);
await shot("B-enter-spam");
const composerAfter = await page.evaluate(() => ({
  taValue: (document.querySelector("textarea"))?.value,
  hasStop: Array.from(document.querySelectorAll("button")).some((b) => (b.textContent || "").trim() === "Stop"),
}));
log(`  after spam: taValue="${(composerAfter.taValue || "").slice(0, 80)}" hasStop=${composerAfter.hasStop}`);

// Wait for whatever's streaming to finish, or stop it
if (composerAfter.hasStop) {
  log(`  stopping the runaway send`);
  await page.getByRole("button", { name: "Stop" }).first().click().catch(() => {});
  await page.waitForTimeout(2000);
}
try {
  await page.getByRole("button", { name: "Send", exact: true }).waitFor({ state: "visible", timeout: 30000 });
} catch { log(`  Send didn't come back in 30s`); }

await ta.fill("");
await page.waitForTimeout(1000);

// ─── C) HTML paste via clipboard evaluate ──────────────────────────────────
log(`\n=== C) HTML paste ===`);
await ta.click();
const htmlPayload = "<script>window.__pwnd=1</script><b>Bold</b> <a href='javascript:alert(1)'>link</a>";
await ta.fill(htmlPayload);
await page.waitForTimeout(400);
await shot("C-html-in-composer");
const pwned = await page.evaluate(() => (window).__pwnd === 1);
log(`  window.__pwnd set? ${pwned} (expected: false — text should not execute)`);
await ta.fill("");

// ─── D) Emoji + RTL mix ────────────────────────────────────────────────────
log(`\n=== D) mixed script and emoji ===`);
const mixed = "🚀 مرحبا 你好 Здравствуйте こんにちは! 🌌×∞ · ∑ = ∫f(x)dx · لغة العرب".repeat(30);
await ta.click();
await ta.fill(mixed);
await page.waitForTimeout(400);
await shot("D-mixed");
const mixedLen = await page.evaluate(() => (document.querySelector("textarea"))?.value?.length);
log(`  composer accepted ${mixedLen} chars of mixed content`);
await ta.fill("");

// ─── Report ────────────────────────────────────────────────────────────────
log(`\n=== errors ===`);
log(`  console errors: ${events.filter((e) => e.type === "console" && e.level === "error").length}`);
log(`  page errors: ${events.filter((e) => e.type === "pageerror").length}`);
for (const e of events.filter((x) => x.type === "pageerror").slice(0, 5)) log(`    PE: ${e.message.slice(0, 200)}`);

writeFileSync(join(runDir, "summary.json"), JSON.stringify({
  bigTextChars: bigText.length,
  errors: events.filter((e) => e.type === "pageerror" || (e.type === "console" && e.level === "error")),
}, null, 2));
log(`\nout: ${runDir}`);
await browser.close();
