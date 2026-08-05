import assert from 'node:assert/strict';
import test from 'node:test';
import { evaluateMobileReportRecords } from '../lib/report_record_policy.mjs';

const cell = (label, text) => ({ width: 320, display: 'block', labelDisplay: 'block', label, text, wordBreak: 'normal', overflowWrap: 'normal', clipped: false });
const passing = { viewportWidth: 390, tableWidth: 320, rows: [{ cells: [cell('Control', 'Network'), cell('Result', 'Denied'), cell('Evidence', 'Runtime policy')] }] };

test('accepts full-width vertical Report records with all fields', () => {
  assert.deepEqual(evaluateMobileReportRecords(passing), []);
  const zoom200 = structuredClone(passing);
  zoom200.tableWidth = 326;
  zoom200.rows[0].cells.forEach((entry) => { entry.width = 289; });
  assert.deepEqual(evaluateMobileReportRecords(zoom200), []);
});

test('rejects clipping, fragmentation, undersized values, and absent result/evidence', () => {
  const mutations = [
    { code: 'clipped-content', mutate: (row) => { row.cells[2].clipped = true; } },
    { code: 'near-character-fragmentation-risk', mutate: (row) => { row.cells[1].wordBreak = 'break-all'; } },
    { code: 'undersized-value-region', mutate: (row) => { row.cells[1].width = 70; } },
    { code: 'undersized-value-region', mutate: (row) => { row.cells[1].width = 270; } },
    { code: 'absent-result-or-evidence', mutate: (row) => { row.cells.pop(); } },
    { code: 'absent-content', mutate: (row) => { row.cells[2].text = ''; } },
    { code: 'not-vertical-record', mutate: (row) => { row.cells[0].display = 'grid'; } },
  ];
  for (const { code, mutate } of mutations) {
    const candidate = structuredClone(passing); mutate(candidate.rows[0]);
    assert.ok(evaluateMobileReportRecords(candidate).some((failure) => failure.code === code), code);
  }
});
