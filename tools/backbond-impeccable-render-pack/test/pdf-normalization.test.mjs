import assert from 'node:assert/strict';
import test from 'node:test';
import { normalizePdfMetadata, PDF_NORMALIZATION_SCHEMA } from '../tools/pdf-normalization.mjs';

const pdf = (date = '20260805001447', body = 'BT /F1 12 Tf (Evidence) Tj ET', title = 'BackBond Report') => Buffer.from(`%PDF-1.4\n1 0 obj\n<</Title (${title})/CreationDate (D:${date}+00'00')/ModDate (D:${date}+00'00')>>\nendobj\n2 0 obj\n<</Length ${body.length}>>stream\n${body}\nendstream\nendobj\n%%EOF`, 'latin1');

test('normalizes only the schema-declared CreationDate and ModDate fields', () => {
  const a = normalizePdfMetadata(pdf('20260805001447'));
  const b = normalizePdfMetadata(pdf('20260805001600'));
  assert.equal(a.schema, PDF_NORMALIZATION_SCHEMA);
  assert.notEqual(a.rawSha256, b.rawSha256);
  assert.equal(a.normalizedSha256, b.normalizedSha256);
});

test('substantive PDF and undeclared metadata mutations change normalized identity', () => {
  const anchor = normalizePdfMetadata(pdf());
  assert.notEqual(anchor.normalizedSha256, normalizePdfMetadata(pdf('20260805001447', 'BT /F1 12 Tf (Changed evidence) Tj ET')).normalizedSha256);
  assert.notEqual(anchor.normalizedSha256, normalizePdfMetadata(pdf('20260805001447', 'BT /F1 12 Tf (Evidence) Tj ET', 'Different title')).normalizedSha256);
});

test('rejects missing or duplicate proven volatile fields', () => {
  assert.throws(() => normalizePdfMetadata(Buffer.from('%PDF-1.4\n%%EOF')), /expected exactly one/);
  const duplicate = Buffer.concat([pdf(), Buffer.from("\n/CreationDate (D:20260805001447+00'00')")]);
  assert.throws(() => normalizePdfMetadata(duplicate), /expected exactly one/);
});
