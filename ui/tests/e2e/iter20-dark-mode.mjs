// Iter 20: dark mode audit — set theme=dark, screenshot each key surface
import { chromium } from "playwright";
import { mkdirSync, appendFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = "http://127.0.0.1:3000";
const MG_URL = `${BASE}/?assistantId=agent_local&resourceId=local&workspaceId=local-d15f5b3aa04d`;
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter20-${stamp}`);
mkdirSync(runDir, { recursive: true });

function log(m) { const l = `[${new Date().toISOString()}] ${m}`; console.log(l); appendFileSync(join(runDir, "run.log"), l + "\n"); }

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  colorScheme: "dark",
});
// Preload localStorage for dark theme
await context.addInitScript(() => {
  try { localStorage.setItem("internagents.theme", "dark"); } catch {}
});

const page = await context.newPage();
let shot = 0;
async function screenshot(name) {
  shot += 1;
  await page.screenshot({ path: join(runDir, `${String(shot).padStart(2, "0")}-${name}.png`), fullPage: false });
  log(`📸 ${shot}-${name}`);
}

log(`🚀 landing`);
await page.goto(`${BASE}/projects`, { waitUntil: "domcontentloaded" });
await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
await page.waitForTimeout(2500);
await screenshot("landing-dark");

// Verify theme is dark
const themeCheck = await page.evaluate(() => ({
  htmlTheme: document.documentElement.dataset.theme,
  hasDarkClass: document.documentElement.classList.contains("dark"),
  bodyBg: getComputedStyle(document.body).backgroundColor,
  bodyColor: getComputedStyle(document.body).color,
}));
log(`  theme state: ${JSON.stringify(themeCheck)}`);

// ─── mg workspace ─────────────────────────────────────────────────────────
log(`\n🚀 open mg (dark)`);
await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3500);
await screenshot("mg-dark");

// Open a session with review cards - click into 一句话：什么是熵？ (has review, has recent conversation)
log(`  click into an existing session with review`);
const anySession = page.locator("button, a").filter({ hasText: /^一句话.{0,15}熵/ }).first();
if (await anySession.isVisible().catch(() => false)) {
  await anySession.click();
  await page.waitForTimeout(2500);
  await screenshot("mg-session-dark");
}

// Scroll to bottom to see review cards
await page.evaluate(() => document.querySelector("main")?.scrollTo({ top: 999999 }));
await page.waitForTimeout(500);
await screenshot("mg-session-bottom-dark");

// Send a quick prompt to trigger new review
const ta = page.locator("textarea").first();
if (await ta.isVisible().catch(() => false)) {
  await ta.click();
  await ta.fill("一句话：什么是奇点？");
  await page.getByRole("button", { name: "Send", exact: true }).click().catch(() => {});
  try {
    await page.getByRole("button", { name: "Send", exact: true }).waitFor({ state: "visible", timeout: 60000 });
  } catch {}
  await page.waitForTimeout(6000);
  await screenshot("mg-new-answer-dark");
}

// ─── Settings ────────────────────────────────────────────────────────────
log(`\n🚀 Settings (dark)`);
await page.goto(`${BASE}/config`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(2500);
await screenshot("settings-dark");
await page.evaluate(() => window.scrollTo({ top: document.body.scrollHeight, behavior: "instant" }));
await page.waitForTimeout(500);
await screenshot("settings-bottom-dark");

// ─── Onboarding wizard (dark) ─────────────────────────────────────────────
log(`\n🚀 new workspace onboarding (dark)`);
const wsPath = `/tmp/e2e_iter20_${Date.now()}`;
mkdirSync(wsPath, { recursive: true });
page.on("dialog", async (d) => { if (d.type() === "prompt") await d.accept(wsPath); else await d.accept(); });
await page.goto(`${BASE}/projects`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(2000);
await page.getByRole("button", { name: "New project", exact: true }).click();
await page.waitForURL(/workspaceId=/, { timeout: 20000 });
await page.waitForTimeout(6000);
await screenshot("wizard-dark");

// ─── Automated color contrast audit ───────────────────────────────────────
log(`\n=== auto contrast audit ===`);
const contrast = await page.evaluate(() => {
  const suspicious = [];
  const all = Array.from(document.querySelectorAll("*"));
  for (const el of all) {
    const text = (el.textContent || "").trim();
    if (!text || text.length > 200 || el.children.length > 0) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 10 || rect.height < 10) continue;
    if (rect.bottom < 0 || rect.top > 900) continue;
    const cs = getComputedStyle(el);
    const fg = cs.color;
    const bg = cs.backgroundColor;
    // parse rgb
    const parse = (s) => { const m = s.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/); return m ? [+m[1], +m[2], +m[3]] : null; };
    const fgRgb = parse(fg);
    if (!fgRgb) continue;
    const lum = (r, g, b) => 0.299 * r + 0.587 * g + 0.114 * b;
    const fgLum = lum(...fgRgb);
    // In dark mode we expect the text to be brightish. If fg is <100 and bg is transparent-inherit-dark, hard to read
    // Just flag pure-white or very dark texts by contrast against document background
    // Effective bg approx: walk up until non-transparent
    let bgRgb = parse(bg);
    let node = el;
    while ((!bgRgb || parse(bg)?.[0] === undefined) && node.parentElement) {
      node = node.parentElement;
      bgRgb = parse(getComputedStyle(node).backgroundColor);
      if (bgRgb) break;
    }
    if (!bgRgb) continue;
    const bgLum = lum(...bgRgb);
    const contrast = Math.abs(fgLum - bgLum);
    if (contrast < 40) {
      suspicious.push({ tag: el.tagName, text: text.slice(0, 60), fg, bg: `rgb(${bgRgb.join(",")})`, contrast: Math.round(contrast) });
    }
  }
  return suspicious.slice(0, 20);
});
log(`suspected low-contrast elements: ${contrast.length}`);
contrast.slice(0, 8).forEach((s) => log(`  ${s.tag} "${s.text}" fg=${s.fg} bg=${s.bg} lum-delta=${s.contrast}`));

writeFileSync(join(runDir, "summary.json"), JSON.stringify({ themeCheck, contrast }, null, 2));
log(`\nout: ${runDir}`);
await browser.close();
