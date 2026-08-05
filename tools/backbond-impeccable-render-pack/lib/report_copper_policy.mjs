const PANEL_NEUTRAL = '.panel{background:#fff;border:1px solid #b9c4bc;padding:1rem}';
const MOBILE_RECORD_NEUTRAL = 'tr{background:#fff;border:1px solid #c8d0ca;margin-bottom:.75rem;padding:.5rem}';
const DECORATIVE_COPPER = /(?:border(?:-left|-inline-start)?\s*:[^;}]*)(?:#b95000|rgb\(\s*185\s*,\s*80\s*,\s*0\s*\)|var\(\s*--copper\s*\))/i;

export function evaluateReportCopperLaw(html) {
  const failures = [];
  const panelStart = html.indexOf('.panel{');
  const mobileStart = html.indexOf('tr{background:#fff', html.indexOf('@media(max-width:700px)'));
  const panelRule = panelStart >= 0 ? html.slice(panelStart, html.indexOf('}', panelStart) + 1) : '';
  const mobileRule = mobileStart >= 0 ? html.slice(mobileStart, html.indexOf('}', mobileStart) + 1) : '';

  if (panelRule !== PANEL_NEUTRAL) failures.push({ code: 'summary-panel-not-neutral', rule: panelRule });
  if (mobileRule !== MOBILE_RECORD_NEUTRAL) failures.push({ code: 'mobile-record-not-neutral', rule: mobileRule });
  if (DECORATIVE_COPPER.test(panelRule)) failures.push({ code: 'decorative-copper-summary-panel' });
  if (DECORATIVE_COPPER.test(mobileRule)) failures.push({ code: 'decorative-copper-mobile-record' });
  return failures;
}
