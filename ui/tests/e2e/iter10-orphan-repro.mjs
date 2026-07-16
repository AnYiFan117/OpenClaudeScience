// Iter 10: minimal repro — real Playwright .click() on 其他 flow
// Compare: card-option click (control) vs 其他+提交 (variable)
import { chromium } from "playwright";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = "http://127.0.0.1:3000";
const RUNTIME = "http://127.0.0.1:2024";
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter10-${stamp}`);
mkdirSync(runDir, { recursive: true });

const events = [];
function log(m) { const l = `[${new Date().toISOString()}] ${m}`; console.log(l); appendFileSync(join(runDir, "run.log"), l + "\n"); }

// Run one workspace scenario: mode = "card" (control) or "other" (variable).
async function runScenario(mode) {
  const wsPath = `/tmp/e2e_iter10_${mode}_${Date.now()}`;
  mkdirSync(wsPath, { recursive: true });
  log(`\n╔══ mode=${mode} ws=${wsPath}`);

  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  page.on("dialog", async (d) => { if (d.type() === "prompt") await d.accept(wsPath); else await d.accept(); });
  page.on("pageerror", (e) => log(`  pageerror: ${e.message}`));
  page.on("response", (r) => { if (r.status() >= 400 && !r.url().includes("/api/workspaces")) log(`  bad resp: ${r.status()} ${r.url()}`); });

  let shot = 0;
  const snap = async (t) => { shot++; await page.screenshot({ path: join(runDir, `${mode}-${String(shot).padStart(2, "0")}-${t}.png`) }); log(`  📸 ${mode}-${shot}-${t}`); };

  await page.goto(`${BASE}/projects`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(3000);
  await page.getByRole("button", { name: "New project", exact: true }).waitFor({ state: "visible", timeout: 20000 });
  await page.getByRole("button", { name: "New project", exact: true }).click();
  await page.waitForURL(/workspaceId=/, { timeout: 20000 });
  await page.locator("textarea").first().waitFor({ state: "visible", timeout: 15000 });
  const workspaceId = new URL(page.url()).searchParams.get("workspaceId");
  const urlThreadIdBefore = new URL(page.url()).searchParams.get("threadId");
  log(`  workspaceId=${workspaceId} urlThreadIdBefore=${urlThreadIdBefore}`);
  await page.waitForTimeout(3000);
  await snap("loaded");

  // Wait for wizard's first ask_user dialog
  const askDialog = page.locator("[role='dialog']").filter({ has: page.locator("[id='ask-user-question']") }).first();
  await askDialog.waitFor({ state: "visible", timeout: 30000 });
  // EXTRA wait: give React time to propagate threadId from onThreadId callback into useChat state
  await page.waitForTimeout(8000);
  const q0 = await askDialog.locator("[id='ask-user-question']").textContent();
  log(`  Q1: ${q0.slice(0, 50)}`);
  await snap("Q1");

  if (mode === "card") {
    // Click first enabled option card
    const opt = askDialog.locator("button").filter({ hasText: /^完整流程/ }).first();
    await opt.waitFor({ state: "visible", timeout: 5000 });
    await opt.click();
    log(`  👉 clicked card "完整流程" (real click)`);
  } else {
    // Click 其他 (real click), fill textarea, click 提交
    const otherBtn = askDialog.locator("button").filter({ hasText: "其他" }).first();
    await otherBtn.waitFor({ state: "visible", timeout: 5000 });
    await otherBtn.click();
    log(`  👉 clicked 其他 (real click)`);
    await page.waitForTimeout(400);
    const dta = askDialog.locator("textarea").first();
    await dta.waitFor({ state: "visible", timeout: 5000 });
    await dta.fill("我做卫星热控设计，主要跟航天器热平衡和相变材料打交道。");
    await page.waitForTimeout(200);
    const submitBtn = askDialog.locator("button").filter({ hasText: "提交" }).first();
    await submitBtn.waitFor({ state: "visible", timeout: 5000 });
    await submitBtn.click();
    log(`  👉 clicked 提交 (real click)`);
  }

  // Wait for wizard to progress (dialog changes)
  await page.waitForTimeout(500);
  await snap("just-clicked");
  try {
    await askDialog.locator("[id='ask-user-question']").filter({ hasNotText: q0 }).waitFor({ state: "visible", timeout: 60000 });
    log(`  ✅ wizard advanced (new question rendered)`);
  } catch {
    log(`  ⚠️  no new question within 60s`);
  }
  await page.waitForTimeout(3000);
  await snap("advanced");

  const urlThreadIdAfter = new URL(page.url()).searchParams.get("threadId");
  log(`  urlThreadIdAfter=${urlThreadIdAfter}`);

  // Now enumerate ALL threads created during this test window
  const searchR = await fetch(`${RUNTIME}/threads/search`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ limit: 20 }),
  });
  const allThreads = await searchR.json();
  const recent = allThreads.filter((t) => new Date(t.created_at).getTime() > Date.now() - 90000);
  log(`  ${recent.length} threads created in last 90s`);
  const perThread = [];
  for (const t of recent) {
    const st = await fetch(`${RUNTIME}/threads/${t.thread_id}/state`);
    const state = await st.json();
    const msgs = state?.values?.messages ?? [];
    const info = {
      thread_id: t.thread_id,
      workspace_id_in_meta: t.metadata?.internagents_workspace_id ?? null,
      graph_id: t.metadata?.graph_id,
      msgCount: msgs.length,
      types: msgs.map((m) => m.type),
      lastToolContent: msgs.filter((m) => m.type === "tool").at(-1)?.content?.slice?.(0, 60) ?? null,
    };
    perThread.push(info);
    log(`    thread ${info.thread_id.slice(0, 8)}: ws_meta=${info.workspace_id_in_meta} graph=${info.graph_id} msgs=${info.msgCount} lastTool="${info.lastToolContent}"`);
  }

  const orphanCount = perThread.filter((t) => t.workspace_id_in_meta === null || t.workspace_id_in_meta === undefined).length;
  const matchedCount = perThread.filter((t) => t.workspace_id_in_meta === workspaceId).length;
  log(`  → orphan threads (no ws metadata): ${orphanCount}, matched threads: ${matchedCount}`);

  await browser.close();
  return { mode, workspaceId, urlThreadIdBefore, urlThreadIdAfter, threads: perThread, orphanCount, matchedCount };
}

// Control: click card option (should be clean)
const control = await runScenario("card");
// Variable: click 其他 + submit
const variable = await runScenario("other");

writeFileSync(join(runDir, "summary.json"), JSON.stringify({ control, variable }, null, 2));

log(`\n╔══ VERDICT`);
log(`  card:  orphans=${control.orphanCount}, matched=${control.matchedCount}`);
log(`  other: orphans=${variable.orphanCount}, matched=${variable.matchedCount}`);
if (variable.orphanCount > 0 && control.orphanCount === 0) {
  log(`  → PRODUCT BUG: 其他 path creates orphan threads while card path doesn't`);
} else if (variable.orphanCount === control.orphanCount) {
  log(`  → NO DIFFERENCE: both paths behave the same`);
} else {
  log(`  → mixed / inconclusive`);
}
log(`out: ${runDir}`);
