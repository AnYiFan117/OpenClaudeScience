// Iter 25: language toggle audit — screenshot key surfaces in zh and en
import { chromium } from "playwright";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = "http://127.0.0.1:3000";
const MG_URL = `${BASE}/?assistantId=agent_local&resourceId=local&workspaceId=local-d15f5b3aa04d`;
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = join(dirname(fileURLToPath(import.meta.url)), "out");
mkdirSync(outDir, { recursive: true });
const runDir = join(outDir, `iter25-${stamp}`);
mkdirSync(runDir, { recursive: true });

function log(m) { console.log(`[${new Date().toISOString()}] ${m}`); appendFileSync(join(runDir, "run.log"), `[${new Date().toISOString()}] ${m}\n`); }

async function scanSurface(page, label) {
  await page.waitForTimeout(2500);
  const texts = await page.evaluate(() => {
    const seen = new Set();
    const result = [];
    for (const el of document.querySelectorAll("button, span, div, p, h1, h2, h3, label")) {
      const t = (el.textContent || "").trim();
      if (!t || t.length > 60 || t.length < 2 || el.children.length > 0) continue;
      // Skip pure numerals or ids
      if (/^[0-9\s.:kMB%×—Ctx]+$/.test(t)) continue;
      if (seen.has(t)) continue;
      seen.add(t);
      result.push(t);
    }
    return result;
  });
  return { label, count: texts.length, sample: texts };
}

async function withLang(colorScheme, lang, taskFn) {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.addInitScript((l) => {
    try { localStorage.setItem("internagents.ui.language", l); } catch {}
  }, lang);
  const page = await context.newPage();
  const result = await taskFn(page);
  await browser.close();
  return result;
}

const surfaces = {};
for (const lang of ["zh", "en"]) {
  log(`\n=== lang=${lang} ===`);
  surfaces[lang] = await withLang(null, lang, async (page) => {
    const s = {};
    // landing
    await page.goto(`${BASE}/projects`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("body[data-internagents-startup='ready']", { timeout: 20000 }).catch(() => {});
    await page.screenshot({ path: join(runDir, `${lang}-1-landing.png`) });
    s.landing = await scanSurface(page, "landing");

    // mg workspace
    await page.goto(MG_URL, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(3500);
    await page.screenshot({ path: join(runDir, `${lang}-2-mg.png`) });
    s.mg = await scanSurface(page, "mg");

    // settings
    await page.goto(`${BASE}/config`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2500);
    await page.screenshot({ path: join(runDir, `${lang}-3-settings.png`), fullPage: true });
    s.settings = await scanSurface(page, "settings");

    return s;
  });
  log(`  scanned: ${Object.entries(surfaces[lang]).map(([k,v]) => `${k}=${v.count}`).join(", ")}`);
}

// ─── Compare ──────────────────────────────────────────────────────────────
log(`\n=== analysis: strings that appear IDENTICAL in both zh and en ===`);
for (const surface of ["landing", "mg", "settings"]) {
  const zhSet = new Set(surfaces.zh[surface].sample);
  const enSet = new Set(surfaces.en[surface].sample);
  const both = [...zhSet].filter((s) => enSet.has(s));
  // Strings that legitimately don't need translation: brand names, numbers, English identifiers, URLs
  const suspicious = both.filter((s) => {
    // Skip if contains latin letters — English strings that stay English are fine
    if (/^[A-Za-z0-9\s.·—→\-/:]+$/.test(s)) return false;
    // Skip brand names, project names
    if (/天玄|千枢|InternAgent|deepseek|OpenAI|Anthropic/.test(s)) return false;
    return true;
  });
  log(`  ${surface}: total-identical=${both.length}, suspicious=${suspicious.length}`);
  suspicious.slice(0, 10).forEach((s) => log(`    ${JSON.stringify(s)}`));
}

// Also — strings in zh mode that are pure English (should probably be zh)
log(`\n=== zh mode strings that look English-only (potential untranslated) ===`);
for (const surface of ["landing", "mg", "settings"]) {
  const zhOnly = surfaces.zh[surface].sample.filter((s) => {
    // Pure ASCII string of >4 chars — likely English UI text that didn't translate to zh
    if (!/^[A-Za-z][A-Za-z0-9\s.,'()/\-]{4,}$/.test(s)) return false;
    // Skip brand names, tech terms
    if (/GitHub|SSH|MCP|SCP|OpenAI|API|json|GB|MB|KB|OpenAI compatible|MCP Servers|SCP Hub/i.test(s)) return false;
    return true;
  });
  log(`  ${surface}: ${zhOnly.length} pure-English strings`);
  zhOnly.slice(0, 12).forEach((s) => log(`    ${JSON.stringify(s)}`));
}

// And strings in en mode that are pure Chinese (probably untranslated)
log(`\n=== en mode strings that look Chinese-only (untranslated) ===`);
for (const surface of ["landing", "mg", "settings"]) {
  const enOnly = surfaces.en[surface].sample.filter((s) => /[一-龥]/.test(s));
  const cnPure = enOnly.filter((s) => {
    if (/天玄|千枢|中文|中国航天|重启|开始使用/.test(s)) return false; // brand + expected zh options
    return true;
  });
  log(`  ${surface}: ${cnPure.length} zh-only strings (excluding brand)`);
  cnPure.slice(0, 12).forEach((s) => log(`    ${JSON.stringify(s)}`));
}

writeFileSync(join(runDir, "summary.json"), JSON.stringify(surfaces, null, 2));
log(`\nout: ${runDir}`);
