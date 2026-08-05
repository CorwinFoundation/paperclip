import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import { evaluateReportCopperLaw } from '../lib/report_copper_policy.mjs';

const report = fs.readFileSync(new URL('../benchmark/report/revision/index.html', import.meta.url), 'utf8');

test('Report summary panels and mobile records use exact neutral document hairlines', () => {
  assert.deepEqual(evaluateReportCopperLaw(report), []);
});

test('rejects decorative copper rail near misses in both authorized scopes', () => {
  const mutations = [
    report.replace('border:1px solid #b9c4bc', 'border-left:5px solid #B95000'),
    report.replace('border:1px solid #b9c4bc', 'border-inline-start:5px solid rgb(185, 80, 0)'),
    report.replace('border:1px solid #c8d0ca', 'border-left:5px solid var(--copper)'),
    report.replace('border:1px solid #c8d0ca', 'border:5px solid #b95000'),
  ];
  for (const candidate of mutations) {
    assert.notDeepEqual(evaluateReportCopperLaw(candidate), []);
  }
});
