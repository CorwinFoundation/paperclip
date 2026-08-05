#!/usr/bin/env node
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(process.argv[2] || 'evidence');
const results = [];
for (const surface of ['marketing', 'report', 'scanner']) {
  const auditPath = path.join(root, surface, 'revision', 'audit.json');
  const audit = JSON.parse(fs.readFileSync(auditPath));
  for (const entry of audit.evidence) {
    assert.equal(entry.normal.lang, 'en', `${surface}/${entry.viewport}: lang`);
    assert.ok(entry.normal.headingOrder[0]?.level === 1, `${surface}/${entry.viewport}: first heading must be h1`);
    assert.ok(entry.normal.controls.every((control) => control.name), `${surface}/${entry.viewport}: unnamed control`);
    assert.deepEqual(entry.normal.overflow, [], `${surface}/${entry.viewport}: overflow`);
    assert.deepEqual(entry.zoom200.overflow, [], `${surface}/${entry.viewport}: 200% overflow`);
    assert.deepEqual(entry.normal.contrastFailures, [], `${surface}/${entry.viewport}: contrast`);
    assert.deepEqual(entry.normal.reducedMotionViolations, [], `${surface}/${entry.viewport}: reduced motion`);
    assert.deepEqual(entry.normal.reportRecordFailures, [], `${surface}/${entry.viewport}: report mobile records`);
    assert.deepEqual(entry.zoom200.reportRecordFailures, [], `${surface}/${entry.viewport}: report mobile records at 200%`);
    assert.equal(entry.focus.focusableStops, entry.focus.visibleStops, `${surface}/${entry.viewport}: focus visibility`);
    results.push({ surface, viewport: entry.viewport, lang: entry.normal.lang, controls: entry.normal.controls, headings: entry.normal.headingOrder, pass: true });
  }
}
fs.writeFileSync(path.join(root, 'accessibility-summary.json'), `${JSON.stringify({ schema: 'backbond-accessibility-summary-v1', results }, null, 2)}\n`);
