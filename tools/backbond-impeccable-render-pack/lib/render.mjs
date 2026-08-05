import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright-core';
import { EXACT_HIDDEN_COLUMN_HEADER_CONTRACT } from './a11y_exemptions.mjs';
import { inventory, rejectRemoteHtml, sha256 } from './policy.mjs';
import { evaluateMobileReportRecords } from './report_record_policy.mjs';

const VIEWPORTS = Object.freeze([
  { id: 'desktop', width: 1440, height: 900 },
  { id: 'tablet', width: 768, height: 1024 },
  { id: 'mobile-390', width: 390, height: 844 },
]);
const executablePath = '/ms-playwright/chromium-1187/chrome-linux/chrome';

const auditPage = (hiddenColumnHeaderContract) => {
  const rgb = (value) => (value.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
  const luminance = (value) => {
    const channels = rgb(value).map((channel) => { const normalized = channel / 255; return normalized <= 0.03928 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4; });
    return channels.length === 3 ? 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2] : null;
  };
  const contrastFailures = [...document.querySelectorAll('body *')].filter((el) => (el.textContent || '').trim() && el.children.length === 0).flatMap((el) => {
    const style = getComputedStyle(el);
    const foreground = luminance(style.color);
    let ancestor = el;
    let background = null;
    while (ancestor && background === null) {
      const color = getComputedStyle(ancestor).backgroundColor;
      if (!color.endsWith(', 0)') && color !== 'rgba(0, 0, 0, 0)') background = luminance(color);
      ancestor = ancestor.parentElement;
    }
    if (foreground === null || background === null) return [];
    const ratio = (Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05);
    const threshold = parseFloat(style.fontSize) >= 24 || (parseFloat(style.fontSize) >= 18.66 && Number(style.fontWeight) >= 700) ? 3 : 4.5;
    return ratio + 0.001 < threshold ? [{ tag: el.tagName.toLowerCase(), ratio: Number(ratio.toFixed(2)), threshold }] : [];
  });
  const focusables = [...document.querySelectorAll('a[href],button,input,select,textarea,[tabindex]:not([tabindex="-1"])')].filter((el) => !el.disabled);
  const controls = focusables.map((el) => {
    const labelledBy = el.getAttribute('aria-labelledby');
    const label = el.labels?.[0]?.textContent?.trim()
      || el.getAttribute('aria-label')
      || (labelledBy ? labelledBy.split(/\s+/).map((id) => document.getElementById(id)?.textContent?.trim() || '').join(' ').trim() : '')
      || el.textContent?.trim()
      || el.getAttribute('title')
      || '';
    return { tag: el.tagName.toLowerCase(), id: el.id || null, name: label };
  });
  const landmarks = ['header', 'nav', 'main', 'footer'].map((tag) => ({ tag, count: document.querySelectorAll(tag).length }));
  const clipped = [...document.querySelectorAll('body *')].filter((el) => {
    const style = getComputedStyle(el);
    const mode = style.overflowX;
    const descriptor = { tag: el.tagName.toLowerCase(), position: style.position, clip: style.clip, clipPath: style.clipPath, width: style.width, height: style.height, overflowX: mode, ariaHidden: el.getAttribute('aria-hidden'), columnHeaderCount: el.querySelectorAll(':scope > tr > th[scope="col"]').length, totalHeaderCells: el.querySelectorAll(':scope > tr > th').length, rowCount: el.querySelectorAll(':scope > tr').length };
    const exactSemanticHeader = Object.entries(hiddenColumnHeaderContract).every(([key, value]) => key === 'minimumColumnHeaders' || descriptor[key] === value)
      && descriptor.columnHeaderCount >= hiddenColumnHeaderContract.minimumColumnHeaders
      && descriptor.columnHeaderCount === descriptor.totalHeaderCells && descriptor.rowCount === 1;
    if (exactSemanticHeader) return false;
    return ['hidden', 'clip'].includes(mode) && el.scrollWidth > el.clientWidth + 1;
  }).map((el) => el.tagName.toLowerCase()).sort();
  const overflow = document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 ? ['document-horizontal-overflow', ...clipped] : clipped;
  const reducedMotionViolations = [...document.querySelectorAll('body *')].filter((el) => {
    const style = getComputedStyle(el);
    return parseFloat(style.animationDuration) > 0.01 || parseFloat(style.transitionDuration) > 0.01;
  }).map((el) => el.tagName.toLowerCase()).sort();
  const reportRecordDescriptor = [...document.querySelectorAll('#observations table')].map((table) => ({ viewportWidth: innerWidth, tableWidth: table.getBoundingClientRect().width, rows: [...table.querySelectorAll('tbody tr')].map((row) => ({ cells: [...row.querySelectorAll('td')].map((cell) => { const style = getComputedStyle(cell); const label = getComputedStyle(cell, '::before'); return { width: cell.getBoundingClientRect().width, display: style.display, labelDisplay: label.display, label: label.content.replace(/^['\"]|['\"]$/g, ''), text: cell.textContent || '', wordBreak: style.wordBreak, overflowWrap: style.overflowWrap, clipped: cell.scrollWidth > cell.clientWidth + 1 || cell.scrollHeight > cell.clientHeight + 1 }; }) })) }))[0] || { viewportWidth: innerWidth, tableWidth: 0, rows: [] };
  return { lang: document.documentElement.lang || null, title: document.title, controls, focusableCount: focusables.length, landmarks, headingOrder: [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].map((el) => ({ level: Number(el.tagName.slice(1)), text: el.textContent.trim() })), overflow, reducedMotionViolations, contrastFailures, reportRecordDescriptor };
};

export async function render({ input, output }) {
  const files = inventory(input);
  rejectRemoteHtml(input);
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ executablePath, headless: true, args: ['--disable-dev-shm-usage', '--disable-background-networking', '--disable-component-update', '--disable-sync', '--metrics-recording-only', '--no-first-run', '--no-sandbox'] });
  const evidence = [];
  try {
    for (const viewport of VIEWPORTS) {
      const context = await browser.newContext({ viewport, reducedMotion: 'reduce', colorScheme: 'light', locale: 'en-US', timezoneId: 'UTC' });
      await context.route(/^(?!file:)/, (route) => route.abort('blockedbyclient'));
      const page = await context.newPage();
      await page.goto(`file://${path.join(input, 'index.html')}`, { waitUntil: 'load', timeout: 15_000 });
      const keyboard = [];
      for (let i = 0; i < 12; i++) {
        await page.keyboard.press('Tab');
        keyboard.push(await page.evaluate(() => { const el = document.activeElement; const style = getComputedStyle(el); return { target: `${el?.tagName?.toLowerCase() || 'none'}#${el?.id || ''}`, focusVisible: style.outlineStyle !== 'none' && style.outlineWidth !== '0px' || style.boxShadow !== 'none' }; }));
      }
      const focus = { focusableStops: keyboard.filter((item) => item.target !== 'body#').length, visibleStops: keyboard.filter((item) => item.focusVisible).length };
      const normal = await page.evaluate(auditPage, EXACT_HIDDEN_COLUMN_HEADER_CONTRACT);
      normal.reportRecordFailures = evaluateMobileReportRecords(normal.reportRecordDescriptor);
      await page.evaluate(() => { document.documentElement.style.setProperty('font-size', '200%', 'important'); });
      const zoom200 = await page.evaluate(auditPage, EXACT_HIDDEN_COLUMN_HEADER_CONTRACT);
      zoom200.reportRecordFailures = evaluateMobileReportRecords(zoom200.reportRecordDescriptor);
      const png = path.join(output, `${viewport.id}.png`);
      await page.screenshot({ path: png, fullPage: true, animations: 'disabled', caret: 'hide' });
      evidence.push({ viewport: viewport.id, width: viewport.width, height: viewport.height, keyboard, focus, normal, zoom200, artifact: path.basename(png), sha256: sha256(fs.readFileSync(png)) });
      await context.close();
    }
    const context = await browser.newContext({ viewport: VIEWPORTS[0], reducedMotion: 'reduce', locale: 'en-US', timezoneId: 'UTC' });
    await context.route(/^(?!file:)/, (route) => route.abort('blockedbyclient'));
    const page = await context.newPage();
    await page.goto(`file://${path.join(input, 'index.html')}`, { waitUntil: 'load', timeout: 15_000 });
    await page.emulateMedia({ media: 'print', reducedMotion: 'reduce' });
    const pdf = path.join(output, 'print.pdf');
    await page.pdf({ path: pdf, format: 'A4', printBackground: true, preferCSSPageSize: true, tagged: true, outline: true });
    await context.close();
    const audit = { schema: 'backbond-render-audit-v1', runtime: { node: process.version, playwright: '1.55.0', chromium: await browser.version() }, input: files, evidence, print: { artifact: 'print.pdf', reproducibility: 'PDF metadata excluded from deterministic audit hash' }, humanJudgmentRequired: ['brand recognition', 'genericness', 'visual hierarchy', 'historical-claim correctness'], automated: ['keyboard traversal', '200% text zoom overflow', 'reduced motion', 'landmarks', 'focus visibility', 'contrast'] };
    fs.writeFileSync(path.join(output, 'audit.json'), `${JSON.stringify(audit, null, 2)}\n`, { mode: 0o600, flag: 'wx' });
  } finally {
    await browser.close();
  }
}
