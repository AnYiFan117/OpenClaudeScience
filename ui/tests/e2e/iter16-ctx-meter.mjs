// Iter 16: Context meter accuracy
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
const runDir = join(outDir, `iter16-${stamp}`);
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

async function readCtx() {
  return await page.evaluate(() => {
    // find element whose text starts with "Ctx"
    const el = Array.from(document.querySelectorAll("div")).find((d) => {
      const t = (d.textContent || "").trim();
      return t.startsWith("Ctx") && t.length < 60;
    });
    if (!el) return null;
    const label = (el.textContent || "").trim();
    const bar = el.querySelector("div > div");
    const barWidth = bar ? (bar).style.width : null;
    const barClasses = bar ? bar.className : null;
    return { label, barWidth, barClasses, title: el.getAttribute("title") };
  });
}
async function currentThreadId() { return new URL(page.url()).searchParams.get("threadId"); }
async function fetchTokens(tid) {
  const r = await fetch(`${RUNTIME}/threads/${tid}/state`);
  if (!r.ok) return null;
  const s = await r.json();
  return { contextWindow: s?.values?.contextWindow, contextTokensUsed: s?.values?.contextTokensUsed };
}

log(`🚀 open mg + new session`);
await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
await page.locator("textarea").first().waitFor({ state: "visible", timeout: 15000 });
await page.waitForTimeout(2500);
const newBtn = page.getByRole("button", { name: "New", exact: true }).first();
if (await newBtn.isVisible().catch(() => false)) { await newBtn.click(); await page.waitForTimeout(1200); }

// ─── A) Baseline: empty session ────────────────────────────────────────────
log(`\n=== A) fresh session baseline ===`);
const baseline = await readCtx();
log(`  ${JSON.stringify(baseline)}`);
await shot("A-baseline");

// ─── B) Send a series of progressively longer messages ─────────────────────
log(`\n=== B) 4 turns with growing prompts ===`);
const ta = page.locator("textarea").first();
const prompts = [
  "一句话：什么是熵？",
  "详细解释：什么是量子纠缠？给出 3 个具体例子。",
  "长解释：详细描述宇宙微波背景辐射的发现历史、测量方法、和它揭示的宇宙参数（尽可能详细）。",
  "详细写：请写一段 400 字的关于早期宇宙暴胀理论的介绍，包括理论动机、主要预测、以及与观测数据的对比。",
];
const observations = [];
for (const [i, p] of prompts.entries()) {
  await ta.click();
  await ta.fill(p);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  try {
    await page.getByRole("button", { name: "Send", exact: true }).waitFor({ state: "visible", timeout: 90000 });
  } catch { log(`  R${i+1} timeout`); }
  await page.waitForTimeout(3000);
  const ctx = await readCtx();
  const tid = await currentThreadId();
  const backend = tid ? await fetchTokens(tid) : null;
  observations.push({ turn: i + 1, prompt: p.slice(0, 40), ctx, backend });
  log(`  R${i+1}: UI="${ctx?.label}" bar=${ctx?.barWidth} | backend=${JSON.stringify(backend)}`);
  await shot(`B-r${i+1}`);
}

// ─── C) UI vs backend consistency ──────────────────────────────────────────
log(`\n=== C) consistency check ===`);
for (const o of observations) {
  if (!o.backend?.contextTokensUsed) continue;
  const uiMatch = o.ctx?.label.match(/(\d+(?:\.\d+)?)k/);
  if (!uiMatch) { log(`  R${o.turn}: no k pattern in UI label`); continue; }
  const uiK = parseFloat(uiMatch[1]);
  const backendK = o.backend.contextTokensUsed / 1000;
  const drift = Math.abs(uiK - backendK);
  const ok = drift < 0.5; // allow 500-token rounding drift
  log(`  R${o.turn}: UI=${uiK}k backend=${backendK.toFixed(1)}k drift=${drift.toFixed(1)}k ${ok ? "OK" : "DRIFT"}`);
}

writeFileSync(join(runDir, "summary.json"), JSON.stringify({
  baseline, observations,
  consoleErrors: events.filter((e) => e.type === "console" && e.level === "error").length,
  pageErrors: events.filter((e) => e.type === "pageerror").length,
}, null, 2));

log(`\nout: ${runDir}`);
await browser.close();
