import assert from 'node:assert/strict';
import test from 'node:test';
import { matchesExactHiddenColumnHeader } from '../lib/a11y_exemptions.mjs';

const exact = Object.freeze({ tag: 'thead', position: 'absolute', clip: 'rect(0px, 0px, 0px, 0px)', clipPath: 'inset(50%)', width: '1px', height: '1px', overflowX: 'hidden', ariaHidden: null, columnHeaderCount: 3, totalHeaderCells: 3, rowCount: 1 });

test('exempts only the exact accessible visually-hidden column-header contract', () => {
  assert.equal(matchesExactHiddenColumnHeader(exact), true);
});

test('does not exempt clipped, off-viewport, generic-table, or aria-hidden near misses', () => {
  for (const delta of [{ tag: 'div' }, { position: 'fixed' }, { clip: 'auto' }, { clipPath: 'none' }, { width: '2px' }, { height: '2px' }, { overflowX: 'clip' }, { ariaHidden: 'true' }, { columnHeaderCount: 2 }, { totalHeaderCells: 4 }, { rowCount: 2 }]) {
    assert.equal(matchesExactHiddenColumnHeader({ ...exact, ...delta }), false, JSON.stringify(delta));
  }
});
