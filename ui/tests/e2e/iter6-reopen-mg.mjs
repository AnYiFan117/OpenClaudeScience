// Reopen mg (existing workspace) — check for any race/422 at mount time
import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = "http://127.0.0.1:3000";
const MG_URL = `${BASE}/?assistantId=agent_local&resourceId=local&workspaceId=local-d15f5b3aa04d`;
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter6-reopen-mg-${new Date().toISOString().replace(/[:.]/g, "-")}`);
mkdirSync(runDir, { recursive: true });

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();

const captured = [];
page.on("response", async (r) => {
  if (r.status() >= 400) {
    let body = "";
    try { body = (await r.text()).slice(0, 400); } catch {}
    captured.push({ t: Date.now(), method: r.request().method(), url: r.url(), status: r.status(), respBody: body, reqBody: (r.request().postData() || "").slice(0, 400) });
  }
});
page.on("pageerror", (e) => captured.push({ t: Date.now(), type: "pageerror", message: e.message }));

console.log(`goto mg directly`);
await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
await page.locator("textarea").first().waitFor({ state: "visible", timeout: 15000 });
await page.waitForTimeout(4000);
await page.screenshot({ path: join(runDir, "mg-loaded.png") });

console.log(`\n=== FAILURES on reopen ===`);
for (const f of captured) {
  if (f.status) console.log(`  [${f.status}] ${f.method} ${f.url}: ${f.respBody.slice(0, 200)}`);
  else console.log(`  ${f.type}: ${f.message}`);
}

// Also test the tab/pane title now that useChat.ts:1798 is fixed
const titles = await page.evaluate(() => {
  const tabs = Array.from(document.querySelectorAll("[role='tab']")).map((t) => (t.textContent || "").trim().slice(0, 40));
  const paneTitle = document.querySelector("main h1, main [class*='title']")?.textContent?.trim().slice(0, 60) || null;
  const allH1s = Array.from(document.querySelectorAll("h1, h2")).map((h) => (h.textContent || "").trim().slice(0, 60));
  return { tabs, paneTitle, allH1s };
});
console.log(`\ntitles: ${JSON.stringify(titles)}`);
writeFileSync(join(runDir, "captured.json"), JSON.stringify(captured, null, 2));
await browser.close();
