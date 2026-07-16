import { chromium } from "playwright";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = process.env.BASE_URL || "http://127.0.0.1:3000";
const RUNTIME = process.env.RUNTIME_URL || "http://127.0.0.1:2024";
const MG_URL = `${BASE}/?assistantId=agent_local&resourceId=local&workspaceId=local-d15f5b3aa04d`;
const WSID = "local-d15f5b3aa04d";

const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter4-${stamp}`);
mkdirSync(runDir, { recursive: true });

const events = [];
const logPath = join(runDir, "run.log");
function log(m) { const l = `[${new Date().toISOString()}] ${m}`; console.log(l); appendFileSync(logPath, l + "\n"); }
function evt(t, d) { events.push({ t: new Date().toISOString(), type: t, ...d }); }

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();

// Auto-accept any confirm dialog (some destructive ops probably have confirm)
page.on("dialog", async (d) => { log(`🗨️  dialog(${d.type()}): ${d.message().slice(0, 100)}`); await d.accept(); });
page.on("console", (m) => evt("console", { level: m.type(), text: m.text().slice(0, 500) }));
page.on("pageerror", (e) => evt("pageerror", { message: e.message }));
page.on("response", (r) => { if (r.status() >= 400) evt("badResp", { url: r.url(), status: r.status() }); });
page.on("requestfailed", (r) => evt("reqFail", { url: r.url(), reason: r.failure()?.errorText }));

let shotIdx = 0;
async function shot(name) {
  shotIdx += 1;
  const label = `${String(shotIdx).padStart(2, "0")}-${name}`;
  await page.screenshot({ path: join(runDir, `${label}.png`), fullPage: false });
  log(`📸 ${label}`);
}

async function newestThread() {
  const r = await fetch(`${RUNTIME}/threads/search`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ limit: 5, metadata: { internagents_workspace_id: WSID } }),
  });
  return (await r.json())?.[0] ?? null;
}
async function fetchThreadState(tid) {
  const r = await fetch(`${RUNTIME}/threads/${tid}/state`);
  if (!r.ok) return { error: r.status };
  const s = await r.json();
  return {
    messageCount: (s?.values?.messages ?? []).length,
    reviewCount: (s?.values?.reviews ?? []).length,
    types: (s?.values?.messages ?? []).map((m) => m?.type),
    reviews: (s?.values?.reviews ?? []).map((r) => ({ verdict: r.verdict, status: r.status, at: r.at_message_index })),
    lastAI: (() => {
      const ai = (s?.values?.messages ?? []).findLast?.((m) => m?.type === "ai") || null;
      return ai ? { hasContent: !!(typeof ai.content === "string" ? ai.content : JSON.stringify(ai.content || "")).length, contentPreview: (typeof ai.content === "string" ? ai.content : JSON.stringify(ai.content || "")).slice(0, 100), toolCalls: (ai.tool_calls || []).length } : null;
    })(),
  };
}

log(`🚀 open mg`);
await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
await page.locator("textarea").first().waitFor({ state: "visible", timeout: 15000 });
await page.waitForTimeout(1200);
await shot("mg-landing");

// Find existing "请一句话介绍你自己。" session in sidebar — that's the target we'll switch to.
const sidebarSessions = await page.evaluate(() => {
  const nodes = Array.from(document.querySelectorAll("a, button")).filter((el) => {
    const rect = el.getBoundingClientRect();
    return rect.x < 320 && rect.y > 100 && rect.y < 800 && (el.textContent || "").trim().length > 3;
  });
  return nodes.map((el) => ({ text: (el.textContent || "").trim().slice(0, 60), x: Math.round(el.getBoundingClientRect().x), y: Math.round(el.getBoundingClientRect().y), tag: el.tagName.toLowerCase() }));
});
log(`sidebar candidates: ${JSON.stringify(sidebarSessions.slice(0, 8))}`);

// Fire a slow tool-use prompt to get long streaming
const newBtn = page.getByRole("button", { name: "New", exact: true }).first();
if (await newBtn.isVisible().catch(() => false)) {
  await newBtn.click();
  await page.waitForTimeout(800);
}
const ta = page.locator("textarea").first();
await ta.click();
await ta.fill("请调用工具列出当前工作区 output/ 目录下的**全部** 15 个文件，逐个文件说明其可能的科研含义（每个 2-3 句话）。");
log(`📤 send slow prompt`);
const tSend = Date.now();
await page.getByRole("button", { name: "Send", exact: true }).click();

// Wait for streaming state
const stopBtn = page.getByRole("button", { name: "Stop", exact: true });
await stopBtn.waitFor({ state: "visible", timeout: 10000 });
log(`✅ streaming started ${Date.now() - tSend} ms after Send`);
await page.waitForTimeout(2500);
await shot("streaming");

// Grab the current thread ID before we mess with things
const cur = await newestThread();
const streamingThreadId = cur?.thread_id;
log(`🧵 streaming thread: ${streamingThreadId}`);

// ─── A) Click into a different session in the sidebar (mid-stream) ─────────
log(`\n=== A) mid-stream sidebar click into another session ===`);
// Find a sidebar session that ISN'T the currently active one
const targetSession = sidebarSessions.find((s) => s.text.includes("请一句话介绍你自己"));
if (!targetSession) {
  log(`❌ no target session found in sidebar, using coords near old sessions`);
}
const clickTarget = targetSession || sidebarSessions.find((s) => s.y > 400 && s.y < 700);
log(`👉 click sidebar item: ${JSON.stringify(clickTarget)}`);
if (clickTarget) {
  await page.mouse.click(clickTarget.x + 30, clickTarget.y + 10);
} else {
  log(`❌ nothing clickable`);
}
await page.waitForTimeout(1500);
await shot("A-after-switch");

// What does the page look like now?
const stateA = await page.evaluate(() => {
  const stopBtn = Array.from(document.querySelectorAll("button")).some((b) => (b.textContent || "").trim() === "Stop");
  const sendBtn = Array.from(document.querySelectorAll("button")).some((b) => (b.textContent || "").trim() === "Send");
  const tabs = Array.from(document.querySelectorAll("[role='tab']")).map((t) => (t.textContent || "").trim().slice(0, 40));
  const title = document.querySelector("main")?.querySelector("h1, h2, [class*='title']")?.textContent?.trim().slice(0, 60);
  const bodyExcerpt = document.body.innerText.slice(0, 500);
  return { stopBtn, sendBtn, tabs, title, bodyExcerpt };
});
log(`state A (after switch): sendBtn=${stateA.sendBtn} stopBtn=${stateA.stopBtn} tabs=${JSON.stringify(stateA.tabs)}`);

// Check backend — is the original stream still running?
const backendA = await fetchThreadState(streamingThreadId);
log(`backend state of streaming thread: ${JSON.stringify(backendA)}`);

// ─── B) Click back into the streaming tab ─────────────────────────────────
log(`\n=== B) click back to streaming session ===`);
// Find the tab labeled with our slow prompt
const streamingTab = await page.locator("[role='tab']").first();
await streamingTab.click().catch(() => {});
await page.waitForTimeout(1500);
await shot("B-back-to-streaming");

const stateB = await page.evaluate(() => {
  const stopBtn = Array.from(document.querySelectorAll("button")).some((b) => (b.textContent || "").trim() === "Stop");
  const sendBtn = Array.from(document.querySelectorAll("button")).some((b) => (b.textContent || "").trim() === "Send");
  const runningPh = document.body.innerText.includes("Running...");
  return { stopBtn, sendBtn, runningPh };
});
log(`state B (back to streaming tab): ${JSON.stringify(stateB)}`);

// ─── C) Navigate to Projects list mid-stream ──────────────────────────────
log(`\n=== C) click 'Projects' back-link mid-stream ===`);
const projectsLink = page.getByRole("link", { name: "Projects", exact: true }).first();
if (await projectsLink.isVisible().catch(() => false)) {
  await projectsLink.click();
  await page.waitForTimeout(2000);
  await shot("C-projects");
  const url = page.url();
  log(`current URL: ${url}`);
  const backendC = await fetchThreadState(streamingThreadId);
  log(`backend state of orphan streaming thread: ${JSON.stringify(backendC)}`);
} else {
  log(`❌ Projects link not visible`);
}

// ─── D) Navigate back into mg to see final state ──────────────────────────
log(`\n=== D) navigate back to mg and check final state ===`);
await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
await page.locator("textarea").first().waitFor({ state: "visible", timeout: 15000 });
await page.waitForTimeout(2000);
await shot("D-back-to-mg");

// Wait for any pending stream on that thread to fully settle server-side
await new Promise((r) => setTimeout(r, 15000));
const backendFinal = await fetchThreadState(streamingThreadId);
log(`FINAL backend state of streaming thread: ${JSON.stringify(backendFinal)}`);
await shot("D-final");

// ─── Report ────────────────────────────────────────────────────────────────
writeFileSync(join(runDir, "events.json"), JSON.stringify(events, null, 2));
writeFileSync(join(runDir, "summary.json"), JSON.stringify({
  streamingThread: streamingThreadId,
  stateA_afterSwitch: stateA, backendA,
  stateB_backToStreaming: stateB,
  finalBackend: backendFinal,
  counts: {
    consoleErrors: events.filter((e) => e.type === "console" && e.level === "error").length,
    pageErrors: events.filter((e) => e.type === "pageerror").length,
    badResponses: events.filter((e) => e.type === "badResp").length,
    requestFailures: events.filter((e) => e.type === "reqFail").length,
  },
}, null, 2));
log(`\nout: ${runDir}`);
await browser.close();
