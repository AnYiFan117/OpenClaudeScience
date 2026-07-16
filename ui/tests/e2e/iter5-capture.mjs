// Minimal replay: mount workspace, capture failing request URL + body
import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = "http://127.0.0.1:3000";
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter5-req-capture-${new Date().toISOString().replace(/[:.]/g, "-")}`);
mkdirSync(runDir, { recursive: true });

const wsPath = `/tmp/e2e_iter5b_${Date.now()}`;
mkdirSync(wsPath, { recursive: true });

const browser = await chromium.launch();
const context = await browser.newContext();
const page = await context.newPage();

const captured = [];
page.on("dialog", async (d) => { if (d.type() === "prompt") await d.accept(wsPath); else await d.accept(); });
page.on("request", (req) => {
  const url = req.url();
  if (/threads|assistants|runs|api\/workspaces/.test(url) && url.includes(":3000") || url.includes(":2024")) {
    let body = null;
    try { body = req.postData(); } catch {}
    captured.push({ t: Date.now(), method: req.method(), url, body });
  }
});
page.on("response", async (r) => {
  if (r.status() >= 400) {
    let body = "";
    try { body = (await r.text()).slice(0, 500); } catch {}
    captured.push({ t: Date.now(), method: r.request().method(), url: r.url(), status: r.status(), respBody: body, reqBody: r.request().postData() });
  }
});

console.log(`goto ${BASE}`);
await page.goto(BASE, { waitUntil: "domcontentloaded" });
await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
await page.waitForTimeout(1000);
await page.getByRole("button", { name: "New project", exact: true }).click();
await page.waitForURL(/workspaceId=/, { timeout: 20000 });
console.log(`URL after new-project: ${page.url()}`);
await page.waitForTimeout(4000);
await page.screenshot({ path: join(runDir, "state.png") });

const failures = captured.filter((c) => c.status && c.status >= 400);
console.log(`\n=== FAILURES (${failures.length}) ===`);
for (const f of failures) {
  console.log(`  [${f.status}] ${f.method} ${f.url}`);
  console.log(`    reqBody: ${(f.reqBody || "").slice(0, 300)}`);
  console.log(`    respBody: ${f.respBody.slice(0, 200)}`);
}
writeFileSync(join(runDir, "captured.json"), JSON.stringify(captured, null, 2));
console.log(`\ncurrentURL: ${page.url()}`);
await browser.close();
