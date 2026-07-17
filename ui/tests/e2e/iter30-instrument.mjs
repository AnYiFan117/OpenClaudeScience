// Iter 30: instrument to see WHICH state changes cause the wipe
import { chromium } from "playwright";
import { mkdirSync, appendFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = "http://127.0.0.1:3000";
const MG_URL = `${BASE}/?assistantId=agent_local&resourceId=local&workspaceId=local-d15f5b3aa04d`;
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter30-${stamp}`);
mkdirSync(runDir, { recursive: true });

function log(m) { console.log(`[${new Date().toISOString()}] ${m}`); appendFileSync(join(runDir, "run.log"), `[${new Date().toISOString()}] ${m}\n`); }

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();

await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);
await page.getByRole("button", { name: "New", exact: true }).first().click();
await page.waitForTimeout(2500);

const ta = page.locator("textarea").first();
await ta.click();
await ta.fill("写一个python函数，判断一个字符串是不是回文，保存到 output/palindrome.py。");
await page.getByRole("button", { name: "Send", exact: true }).click();

const deadline = Date.now() + 120000;
while (Date.now() < deadline) {
  const sendVisible = await page.getByRole("button", { name: "Send", exact: true }).isVisible().catch(() => false);
  const stopVisible = await page.getByRole("button", { name: "Stop" }).isVisible().catch(() => false);
  if (sendVisible && !stopVisible) break;
  await page.waitForTimeout(500);
}
await page.waitForTimeout(3000);

// Pull debug samples
const samples = await page.evaluate(() => window.__scopedDebug ?? []);
log(`total debug samples: ${samples.length}`);

// Detect drops: msg count in scopedMsgs drops
let dropIdx = -1;
for (let i = 1; i < samples.length; i++) {
  if (samples[i].scopedMsgs < samples[i - 1].scopedMsgs && samples[i].scopedMsgs === 1) {
    dropIdx = i;
    break;
  }
}
if (dropIdx > 0) {
  log(`\n=== drop detected at sample ${dropIdx} — showing ±5 samples ===`);
  for (let j = Math.max(0, dropIdx - 5); j < Math.min(samples.length, dropIdx + 6); j++) {
    const s = samples[j];
    log(`  [${j}] t=${(s.t - samples[0].t)/1000}s  streamMsgs=${s.streamMsgs} eff=${s.effMsgs} cache=${s.cachedMsgs} scoped=${s.scopedMsgs} streamLoad=${s.streamIsLoading} snapLoad=${s.snapshotIsLoading} snapMsg=${s.snapshotMsgs} snapData=${s.snapshotHasData} isThrLoad=${s.isThreadScopedStateLoading}`);
  }
} else {
  log(`no drop pattern detected — showing last 10 samples`);
  for (let j = Math.max(0, samples.length - 10); j < samples.length; j++) {
    const s = samples[j];
    log(`  [${j}] t=${(s.t - samples[0].t)/1000}s  streamMsgs=${s.streamMsgs} eff=${s.effMsgs} cache=${s.cachedMsgs} scoped=${s.scopedMsgs} streamLoad=${s.streamIsLoading} snapLoad=${s.snapshotIsLoading} snapMsg=${s.snapshotMsgs} snapData=${s.snapshotHasData} isThrLoad=${s.isThreadScopedStateLoading}`);
  }
}

writeFileSync(join(runDir, "debug.json"), JSON.stringify(samples, null, 2));
log(`\nout: ${runDir}`);
await browser.close();
