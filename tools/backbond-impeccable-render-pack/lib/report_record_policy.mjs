export function evaluateMobileReportRecords({ viewportWidth, tableWidth, rows }) {
  if (viewportWidth > 390 || rows.length === 0) return [];
  const failures = [];
  const expectedLabels = ['Control', 'Result', 'Evidence'];
  rows.forEach((row, rowIndex) => {
    if (row.cells.length !== 3) failures.push({ row: rowIndex, code: 'absent-result-or-evidence' });
    row.cells.forEach((cell, cellIndex) => {
      if (!cell.text.trim()) failures.push({ row: rowIndex, cell: cellIndex, code: 'absent-content' });
      if (cell.display !== 'block' || cell.labelDisplay !== 'block') failures.push({ row: rowIndex, cell: cellIndex, code: 'not-vertical-record' });
      if (cell.width < tableWidth * 0.85 || cell.width < Math.min(240, tableWidth * 0.85)) failures.push({ row: rowIndex, cell: cellIndex, code: 'undersized-value-region' });
      if (cell.label !== expectedLabels[cellIndex]) failures.push({ row: rowIndex, cell: cellIndex, code: 'missing-field-label' });
      if (cell.wordBreak !== 'normal' || cell.overflowWrap !== 'normal') failures.push({ row: rowIndex, cell: cellIndex, code: 'near-character-fragmentation-risk' });
      if (cell.clipped) failures.push({ row: rowIndex, cell: cellIndex, code: 'clipped-content' });
    });
  });
  return failures;
}
