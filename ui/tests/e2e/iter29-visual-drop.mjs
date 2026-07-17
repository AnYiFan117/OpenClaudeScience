// Iter 29: screenshot the moment content "disappears" to see what's really happening
import { chromium } from "playwright";
import { mkdirSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = "http://127.0.0.1:3000";
const MG_URL = `${BASE}/?assistantId=agent_local&resourceId=local&workspaceId=local-d15f5b3aa04d`;
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter29-${stamp}`);
mkdirSync(runDir, { recursive: true });

function log(m) { console.log(`[${new Date().toISOString()}] ${m}`); appendFileSync(join(runDir, "run.log"), `[${new Date().toISOString()}] ${m}\n`); }

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();

await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);
await page.getByRole("button", { name: "New", exact: true }).first().click();
await page.waitForTimeout(2500);

// Send one prompt, then screenshot every 500ms for 60 seconds after send
const ta = page.locator("textarea").first();
await ta.click();
await ta.fill("写一个python函数，计算 sqrt(x) 的 Newton 法迭代，保存到 output/newton_sqrt.py。");
await page.getByRole("button", { name: "Send", exact: true }).click();
log(`sent`);

let idx = 0;
const events = [];
const deadline = Date.now() + 90000;
while (Date.now() < deadline) {
  idx += 1;
  const state = await page.evaluate(() => {
    // Focus on the chat messages container specifically
    const chatRoot = document.querySelector("[data-chat-drop-root='true']");
    const container = chatRoot || document.body;
    // The messages are likely rendered inside a scrollable list — get the innermost
    // scrollable child, then measure just its text
    const innerScroll = container.querySelector("[data-radix-scroll-area-viewport]")
      || container.querySelector("[class*='overflow']")
      || container;
    const status = (() => {
      for (const s of document.querySelectorAll("span")) {
        const t = (s.textContent || "").trim();
        if (["Thinking...", "Reviewing...", "正在思考中...", "正在审查中..."].includes(t)) return t;
      }
      return "";
    })();
    return {
      chatText: (innerScroll.innerText || "").length,
      chatTextExcerpt: (innerScroll.innerText || "").slice(0, 100),
      status,
      hasIA: (innerScroll.innerText || "").includes("IA"),
      sendVisible: !!document.querySelector("button[type='submit']") || !!Array.from(document.querySelectorAll("button")).find((b) => (b.textContent||"").trim() === "Send"),
      stopVisible: !!Array.from(document.querySelectorAll("button")).find((b) => (b.textContent||"").trim() === "Stop"),
    };
  }).catch(() => null);
  if (!state) { await page.waitForTimeout(500); continue; }

  events.push({ t: Date.now(), idx, ...state });

  // Screenshot every 2 seconds and at status transitions
  const prev = events.length >= 2 ? events[events.length - 2] : null;
  const shouldShot = idx % 4 === 0
    || (prev && prev.status !== state.status)
    || (prev && Math.abs(prev.chatText - state.chatText) > 100);
  if (shouldShot) {
    const name = `${String(idx).padStart(3,"0")}-t${((Date.now() - events[0].t)/1000).toFixed(1)}s-${state.status.replace(/\.\.\.$/, "").replace(/\s/g, "") || "idle"}-chars${state.chatText}.png`;
    await page.screenshot({ path: join(runDir, name) }).catch(() => {});
  }

  if (state.sendVisible && !state.stopVisible && idx > 8) break;
  await page.waitForTimeout(500);
}

// Wait 15 more seconds and screenshot
for (let i = 0; i < 6; i++) {
  await page.waitForTimeout(2500);
  await page.screenshot({ path: join(runDir, `after-end-${i}.png`) });
}

log(`\n=== timeline (chatText only shows > ±100 changes) ===`);
let last = -1;
for (const e of events) {
  if (Math.abs(e.chatText - last) < 50 && e.status === events[events.indexOf(e) - 1]?.status) continue;
  log(`  +${((e.t - events[0].t)/1000).toFixed(1)}s  status="${e.status}"  chatText=${e.chatText}  hasIA=${e.hasIA}  send=${e.sendVisible} stop=${e.stopVisible}`);
  last = e.chatText;
}
log(`\nout: ${runDir}`);
await browser.close();
