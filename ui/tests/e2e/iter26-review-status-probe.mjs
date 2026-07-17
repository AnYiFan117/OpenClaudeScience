// Iter 26: probe why "正在审查中" doesn't show — dump raw SSE events
// during a real conversation.
import { chromium } from "playwright";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = "http://127.0.0.1:3000";
const MG_URL = `${BASE}/?assistantId=agent_local&resourceId=local&workspaceId=local-d15f5b3aa04d`;
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter26-${stamp}`);
mkdirSync(runDir, { recursive: true });

function log(m) { console.log(`[${new Date().toISOString()}] ${m}`); appendFileSync(join(runDir, "run.log"), `[${new Date().toISOString()}] ${m}\n`); }

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();

// Capture ALL SSE events from the runtime by intercepting response text
const sseEvents = [];
page.on("response", async (r) => {
  if (r.url().includes("/threads/") && r.url().includes("/runs/stream")) {
    log(`  SSE stream opened: ${r.url()}`);
  }
});

// Instrument the tapped stream by injecting console.log after nav
await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);

// Force a fresh session so reviewer definitely runs
const newBtn = page.getByRole("button", { name: "New", exact: true }).first();
if (await newBtn.isVisible().catch(() => false)) {
  await newBtn.click();
  await page.waitForTimeout(2500);
  log(`  clicked New — fresh thread`);
}

// Inject a hook so every custom-mode event is logged to console
await page.evaluate(() => {
  // Naive: subscribe to any custom events by hooking window.fetch responses?
  // Actually simpler: check localStorage / a debug flag. Print current
  // streamEvents state.
  (window).__probeReviewEvents = () => {
    // Try to reach into React state via known root
    const root = document.querySelector("[data-chat-drop-root]");
    return { hasRoot: !!root };
  };
});

// Type a message that's likely to trigger a review
const ta = page.locator("textarea").first();
await ta.click();
const prompt = "帮我用python写一段代码，读取一个csv文件的前10行然后打印每一列的最大值。请完整实现并保存到 output/read_csv.py。";
await ta.fill(prompt);
log(`📤 sending prompt: ${prompt.slice(0, 60)}...`);

// Attach a console listener BEFORE clicking send
page.on("console", (m) => {
  const text = m.text();
  if (text.includes("review") || text.includes("custom") || text.includes("kind")) {
    sseEvents.push({ level: m.type(), text: text.slice(0, 300) });
    log(`  console.${m.type()}: ${text.slice(0, 200)}`);
  }
});

// Also: hook fetch on the client to log SSE frames
await page.evaluate(() => {
  const origFetch = window.fetch;
  (window).__reviewProbe = [];
  window.fetch = async function(...args) {
    const url = args[0]?.toString?.() ?? String(args[0]);
    const res = await origFetch(...args);
    if (url.includes("/threads/") && url.includes("/runs/stream")) {
      // Clone & tee
      const cloned = res.clone();
      const reader = cloned.body?.getReader();
      const dec = new TextDecoder();
      (async () => {
        while (reader) {
          const { value, done } = await reader.read();
          if (done) break;
          const txt = dec.decode(value, { stream: true });
          for (const line of txt.split("\n")) {
            if (line.startsWith("event: ") && line.includes("custom")) {
              (window).__reviewProbe.push({ t: Date.now(), line: line.slice(0, 200) });
              console.log(`[SSE-custom-event]`, line.slice(0, 200));
            }
            if (line.startsWith("data:") && line.includes("review")) {
              (window).__reviewProbe.push({ t: Date.now(), line: line.slice(0, 300) });
              console.log(`[SSE-review-data]`, line.slice(0, 300));
            }
          }
        }
      })();
    }
    return res;
  };
});

await page.getByRole("button", { name: "Send", exact: true }).click();

// Poll for status text
const deadline = Date.now() + 180000; // 3 min
let seenReviewing = false;
let seenThinking = false;
let statusTimeline = [];
let lastStatus = "";
while (Date.now() < deadline) {
  const status = await page.evaluate(() => {
    const spans = Array.from(document.querySelectorAll("span"));
    for (const s of spans) {
      const t = (s.textContent || "").trim();
      if (t.includes("正在思考中") || t.includes("正在审查中") || t.includes("Thinking") || t.includes("Reviewing")) {
        return t;
      }
    }
    return "";
  });
  if (status !== lastStatus) {
    statusTimeline.push({ t: Date.now(), status });
    log(`  ⏱  status changed: "${lastStatus}" → "${status}"`);
    lastStatus = status;
  }
  if (status.includes("审查") || status.includes("Reviewing")) seenReviewing = true;
  if (status.includes("思考") || status.includes("Thinking")) seenThinking = true;

  // Check if we're done
  const sendVisible = await page.getByRole("button", { name: "Send", exact: true }).isVisible().catch(() => false);
  const stopVisible = await page.getByRole("button", { name: "Stop" }).isVisible().catch(() => false);
  if (sendVisible && !stopVisible) {
    log(`  ✅ run finished`);
    break;
  }
  await page.waitForTimeout(500);
}

// Dump probe results
const probe = await page.evaluate(() => (window).__reviewProbe ?? []);
log(`\n=== probe events captured: ${probe.length} ===`);
probe.slice(0, 40).forEach((p, i) => log(`  [${i}] ${p.line}`));

log(`\n=== summary ===`);
log(`  seenThinking: ${seenThinking}`);
log(`  seenReviewing: ${seenReviewing}`);
log(`  status timeline (${statusTimeline.length} changes):`);
statusTimeline.forEach((s) => log(`    ${new Date(s.t).toISOString()}: "${s.status}"`));

writeFileSync(join(runDir, "summary.json"), JSON.stringify({
  seenThinking, seenReviewing, statusTimeline, probeCount: probe.length,
  probeSample: probe.slice(0, 20),
  consoleEvents: sseEvents.slice(0, 30),
}, null, 2));
log(`\nout: ${runDir}`);
await browser.close();
