// Iter 28: probe the "content disappears after review" glitch
// Watches message count in DOM at high frequency around review-done.
import { chromium } from "playwright";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = "http://127.0.0.1:3000";
const MG_URL = `${BASE}/?assistantId=agent_local&resourceId=local&workspaceId=local-d15f5b3aa04d`;
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter28-${stamp}`);
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

// Poll DOM state at 100ms
const timeline = [];
let stopWatch = false;
(async () => {
  while (!stopWatch) {
    const snap = await page.evaluate(() => {
      // Count assistant message blocks + human message blocks
      // React app renders messages in [data-chat-messages] container
      const container = document.querySelector("[data-chat-messages='true']") ?? document.body;
      const messages = container.querySelectorAll("[data-message-id]");
      const status = (() => {
        for (const s of document.querySelectorAll("span")) {
          const t = (s.textContent || "").trim();
          if (["Thinking...", "Reviewing...", "正在思考中...", "正在审查中..."].includes(t)) return t;
        }
        return "";
      })();
      const reviewCards = document.querySelectorAll("[data-review-id], [data-testid='review-card']").length
        || document.querySelectorAll("[class*='ReviewCard']").length;
      // Total text length in message area — captures "content disappeared"
      const textLen = container.innerText?.length ?? 0;
      const hasLoading = /Loading|加载中/i.test(container.innerText || "");
      return { msgCount: messages.length, status, reviewCards, textLen, hasLoading };
    }).catch(() => null);
    if (!snap) { await page.waitForTimeout(100); continue; }
    const prev = timeline[timeline.length - 1];
    const changed = !prev
      || prev.msgCount !== snap.msgCount
      || prev.status !== snap.status
      || prev.reviewCards !== snap.reviewCards
      || Math.abs(prev.textLen - snap.textLen) > 30
      || prev.hasLoading !== snap.hasLoading;
    if (changed) timeline.push({ t: Date.now(), ...snap });
    await page.waitForTimeout(100);
  }
})();

// Send 3 prompts to reliably trigger reviews
const prompts = [
  "写一个python函数，计算斐波那契数列前n项，保存到 output/fib.py。",
  "现在把它改成生成器版本，保存到 output/fib_gen.py。",
  "最后写一个测试脚本 output/test_fib.py，测试上面两个模块。用assert，不用测试框架。",
];

for (let i = 0; i < prompts.length; i++) {
  log(`\n=== prompt ${i + 1} ===`);
  const ta = page.locator("textarea").first();
  await ta.click();
  await ta.fill(prompts[i]);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  const deadline = Date.now() + 180000;
  while (Date.now() < deadline) {
    const sendVisible = await page.getByRole("button", { name: "Send", exact: true }).isVisible().catch(() => false);
    const stopVisible = await page.getByRole("button", { name: "Stop" }).isVisible().catch(() => false);
    if (sendVisible && !stopVisible) break;
    await page.waitForTimeout(200);
  }
  // Keep watching for another 3s after run finishes
  await page.waitForTimeout(3000);
}

stopWatch = true;
await page.waitForTimeout(500);

// Analyze — find msgCount/textLen drops
const drops = [];
for (let i = 1; i < timeline.length; i++) {
  const p = timeline[i - 1], c = timeline[i];
  if (c.msgCount < p.msgCount || c.textLen < p.textLen - 100) {
    drops.push({ prev: p, curr: c, deltaMs: c.t - p.t });
  }
}

log(`\n=== timeline ${timeline.length} entries — showing changes ===`);
for (const t of timeline) {
  log(`  ${new Date(t.t).toISOString().slice(11, 23)}  msgs=${t.msgCount} rvw=${t.reviewCards} text=${t.textLen} loading=${t.hasLoading} status="${t.status}"`);
}

log(`\n=== ⚠️  ${drops.length} content drops detected ===`);
for (const d of drops) {
  log(`  at ${new Date(d.curr.t).toISOString().slice(11, 23)}:  msgs ${d.prev.msgCount}→${d.curr.msgCount}, text ${d.prev.textLen}→${d.curr.textLen}, loading ${d.prev.hasLoading}→${d.curr.hasLoading}, status "${d.prev.status}"→"${d.curr.status}"`);
}

writeFileSync(join(runDir, "summary.json"), JSON.stringify({ timeline, drops }, null, 2));
log(`\nout: ${runDir}`);
await browser.close();
