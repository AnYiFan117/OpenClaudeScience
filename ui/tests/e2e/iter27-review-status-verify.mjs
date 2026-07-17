// Iter 27: verify isReviewing fix — send enough prompts to trigger review
import { chromium } from "playwright";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = "http://127.0.0.1:3000";
const MG_URL = `${BASE}/?assistantId=agent_local&resourceId=local&workspaceId=local-d15f5b3aa04d`;
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter27-${stamp}`);
mkdirSync(runDir, { recursive: true });

function log(m) { console.log(`[${new Date().toISOString()}] ${m}`); appendFileSync(join(runDir, "run.log"), `[${new Date().toISOString()}] ${m}\n`); }

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();

await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);
const newBtn = page.getByRole("button", { name: "New", exact: true }).first();
if (await newBtn.isVisible().catch(() => false)) {
  await newBtn.click();
  await page.waitForTimeout(2500);
  log(`  clicked New`);
}

// Track status text every 200ms
const statusTimeline = [];
let stopTracking = false;
(async () => {
  while (!stopTracking) {
    const st = await page.evaluate(() => {
      for (const s of document.querySelectorAll("span")) {
        const t = (s.textContent || "").trim();
        if (t === "正在思考中..." || t === "正在审查中..." || t === "Thinking..." || t === "Reviewing...") return t;
      }
      return "";
    }).catch(() => "");
    const prev = statusTimeline[statusTimeline.length - 1]?.st;
    if (st !== prev) statusTimeline.push({ t: Date.now(), st });
    await page.waitForTimeout(200);
  }
})();

const prompts = [
  "帮我用python写一段代码，读取一个csv文件的前10行然后打印每一列的最大值。请完整实现并保存到 output/read_csv.py。",
  "现在帮我扩展它，让它也计算每一列的最小值和平均值。保存到 output/read_csv_stats.py。",
  "最后写一个测试脚本 output/test_csv.py，测试上面两个模块的基本功能。要用 assert，不要用测试框架。",
];

for (let i = 0; i < prompts.length; i++) {
  log(`\n=== prompt ${i + 1}/${prompts.length} ===`);
  const ta = page.locator("textarea").first();
  await ta.click();
  await ta.fill(prompts[i]);
  await page.getByRole("button", { name: "Send", exact: true }).click();

  // Wait for run to finish (Send back, Stop gone)
  const deadline = Date.now() + 180000;
  while (Date.now() < deadline) {
    const sendVisible = await page.getByRole("button", { name: "Send", exact: true }).isVisible().catch(() => false);
    const stopVisible = await page.getByRole("button", { name: "Stop" }).isVisible().catch(() => false);
    if (sendVisible && !stopVisible) break;
    await page.waitForTimeout(300);
  }
  log(`  ✅ prompt ${i + 1} done`);
}

stopTracking = true;
await page.waitForTimeout(500);

// Analyse
log(`\n=== status timeline (${statusTimeline.length} transitions) ===`);
const reviewingSegments = [];
let curReviewStart = null;
for (const s of statusTimeline) {
  if (s.st === "正在审查中..." || s.st === "Reviewing...") {
    if (!curReviewStart) curReviewStart = s.t;
  } else {
    if (curReviewStart) {
      reviewingSegments.push({ start: curReviewStart, end: s.t, durationMs: s.t - curReviewStart });
      curReviewStart = null;
    }
  }
  log(`  ${new Date(s.t).toISOString()} → "${s.st}"`);
}
if (curReviewStart) reviewingSegments.push({ start: curReviewStart, end: Date.now(), durationMs: Date.now() - curReviewStart });

log(`\n=== Reviewing segments: ${reviewingSegments.length} ===`);
for (const seg of reviewingSegments) {
  log(`  ${new Date(seg.start).toISOString()} → duration ${seg.durationMs}ms`);
}

// Check thread state for reviews
const threadUrl = page.url();
const threadIdMatch = await page.evaluate(() => {
  // Try to read current thread id from URL search params
  return new URLSearchParams(location.search).get("threadId") || "";
});
log(`\ncurrent url threadId (may be empty): ${threadIdMatch}`);

writeFileSync(join(runDir, "summary.json"), JSON.stringify({
  statusTimeline,
  reviewingSegments,
  reviewingCount: reviewingSegments.length,
  maxReviewMs: Math.max(0, ...reviewingSegments.map((s) => s.durationMs)),
}, null, 2));
log(`\nout: ${runDir}`);
await browser.close();
