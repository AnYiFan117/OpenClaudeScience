// Iter 14: ReviewCard interactions — expand/collapse, initial state, clicks
import { chromium } from "playwright";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = "http://127.0.0.1:3000";
const RUNTIME = "http://127.0.0.1:2024";
const MG_URL = `${BASE}/?assistantId=agent_local&resourceId=local&workspaceId=local-d15f5b3aa04d`;
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter14-${stamp}`);
mkdirSync(runDir, { recursive: true });

const events = [];
function log(m) { const l = `[${new Date().toISOString()}] ${m}`; console.log(l); appendFileSync(join(runDir, "run.log"), l + "\n"); }
function evt(t, d) { events.push({ t: new Date().toISOString(), type: t, ...d }); }

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
page.on("console", (m) => evt("console", { level: m.type(), text: m.text().slice(0, 400) }));
page.on("pageerror", (e) => evt("pageerror", { message: e.message }));

let shotIdx = 0;
async function shot(name) {
  shotIdx += 1;
  await page.screenshot({ path: join(runDir, `${String(shotIdx).padStart(2, "0")}-${name}.png`), fullPage: false });
  log(`📸 ${String(shotIdx).padStart(2, "0")}-${name}`);
}

// Find all review cards on the page with their state
async function reviewCards() {
  return await page.evaluate(() => {
    const cards = Array.from(document.querySelectorAll("div")).filter((el) => {
      const btn = el.querySelector(":scope > button");
      if (!btn) return false;
      const txt = (btn.textContent || "").toLowerCase();
      return txt.includes("review") && /pass|warn|fail|unknown|reviewing/.test(txt);
    });
    return cards.map((c, i) => {
      const rect = c.getBoundingClientRect();
      const btn = c.querySelector(":scope > button");
      const badge = btn?.querySelector("span:nth-of-type(2)");
      const verdict = (badge?.textContent || "").trim();
      const disabled = btn?.disabled ?? false;
      // Detect expanded: presence of body div (children after button)
      const hasBody = c.querySelector(":scope > div") != null;
      const chevron = btn?.querySelector("svg[class*='ChevronUp'], svg[class*='ChevronDown']");
      // Get header y and body y bounds
      return { idx: i, verdict, disabled, hasBody, x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height), btnX: Math.round(btn?.getBoundingClientRect().x ?? 0), btnY: Math.round(btn?.getBoundingClientRect().y ?? 0) };
    });
  });
}

log(`🚀 open mg`);
await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
await page.locator("textarea").first().waitFor({ state: "visible", timeout: 15000 });
await page.waitForTimeout(2500);

// Start fresh session
const newBtn = page.getByRole("button", { name: "New", exact: true }).first();
if (await newBtn.isVisible().catch(() => false)) {
  await newBtn.click();
  await page.waitForTimeout(1000);
}

// Send a prompt that will likely trigger a pass verdict
const ta = page.locator("textarea").first();
await ta.click();
await ta.fill("请一句话解释：什么是普朗克黑体辐射定律？");
await page.getByRole("button", { name: "Send", exact: true }).click();
try {
  await page.getByRole("button", { name: "Send", exact: true }).waitFor({ state: "visible", timeout: 60000 });
} catch { log(`❌ no reply in 60s`); }
await page.waitForTimeout(8000); // wait for reviewer
await shot("after-first-reply");

// Also send iter3's file-listing prompt which historically got warn
await ta.click();
await ta.fill("请调用工具列出当前工作区 output/ 目录下的**全部** 15 个文件，然后用一句话说说其中哪些是 JSON 数据。");
await page.getByRole("button", { name: "Send", exact: true }).click();
try {
  await page.getByRole("button", { name: "Send", exact: true }).waitFor({ state: "visible", timeout: 120000 });
} catch { log(`❌ no reply in 120s`); }
await page.waitForTimeout(10000); // wait for reviewer
await shot("after-tool-reply");

// Scroll to top to see all review cards
await page.evaluate(() => {
  const chatMain = document.querySelector("main");
  chatMain?.scrollTo({ top: 0, behavior: "instant" });
});
await page.waitForTimeout(500);
await shot("scrolled-top");

// ─── Enumerate all review cards ────────────────────────────────────────────
const cards = await reviewCards();
log(`\nfound ${cards.length} review cards:`);
for (const c of cards) log(`  [${c.idx}] verdict=${c.verdict} disabled=${c.disabled} hasBody=${c.hasBody} y=${c.y}`);

// ─── Test interactions ──────────────────────────────────────────────────────
log(`\n=== A) initial state — pass should be collapsed, warn/fail expanded ===`);
for (const c of cards) {
  const initialHeight = await page.evaluate((y) => {
    const cardEl = Array.from(document.querySelectorAll("div")).find((el) => {
      const btn = el.querySelector(":scope > button");
      return btn && Math.abs(el.getBoundingClientRect().y - y) < 5 && (btn.textContent || "").toLowerCase().includes("review");
    });
    return cardEl?.getBoundingClientRect().height ?? -1;
  }, c.y);
  const expectedInitiallyExpanded = /warn|fail/.test(c.verdict);
  const looksExpanded = initialHeight > 60; // header ~40, body adds more
  log(`  card ${c.idx} (${c.verdict}): height=${initialHeight} looksExpanded=${looksExpanded} expected=${expectedInitiallyExpanded} → ${looksExpanded === expectedInitiallyExpanded ? "OK" : "MISMATCH"}`);
}

log(`\n=== B) click each card to toggle ===`);
for (const c of cards) {
  const beforeH = await page.evaluate((y) => {
    const cardEl = Array.from(document.querySelectorAll("div")).find((el) => {
      const btn = el.querySelector(":scope > button");
      return btn && Math.abs(el.getBoundingClientRect().y - y) < 5 && (btn.textContent || "").toLowerCase().includes("review");
    });
    return cardEl?.getBoundingClientRect().height ?? -1;
  }, c.y);
  log(`  card ${c.idx} (${c.verdict}) before click: height=${beforeH} disabled=${c.disabled}`);
  await page.mouse.click(c.btnX + 100, c.btnY + 10);
  await page.waitForTimeout(400);
  const afterH = await page.evaluate((y) => {
    const cardEl = Array.from(document.querySelectorAll("div")).find((el) => {
      const btn = el.querySelector(":scope > button");
      return btn && Math.abs(el.getBoundingClientRect().y - y) < 5 && (btn.textContent || "").toLowerCase().includes("review");
    });
    return cardEl?.getBoundingClientRect().height ?? -1;
  }, c.y);
  const changed = Math.abs(afterH - beforeH) > 10;
  log(`  card ${c.idx} after click: height=${afterH} changed=${changed} ${c.disabled ? "(disabled, expect no change)" : ""}`);
}
await shot("after-clicks");

// ─── Report ────────────────────────────────────────────────────────────────
writeFileSync(join(runDir, "summary.json"), JSON.stringify({
  cards, events: events.length,
  consoleErrors: events.filter((e) => e.type === "console" && e.level === "error").length,
  pageErrors: events.filter((e) => e.type === "pageerror").length,
}, null, 2));
log(`\nout: ${runDir}`);
await browser.close();
