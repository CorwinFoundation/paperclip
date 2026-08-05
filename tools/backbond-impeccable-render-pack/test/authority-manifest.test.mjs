import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import test from 'node:test';

test('authority manifest is source-controlled and keeps promotion closed', () => {
  const bytes = fs.readFileSync('AUTHORITY-MANIFEST.json');
  const manifest = JSON.parse(bytes);
  assert.equal(manifest.schema, 'backbond-phase-two-authority-manifest-v1');
  assert.equal(manifest.rubricVersion, 'phase2-convergence-1.0.0');
  assert.equal(manifest.promotionAuthorized, false);
  assert.match(crypto.createHash('sha256').update(bytes).digest('hex'), /^[0-9a-f]{64}$/);
});

test('candidate builder derives authority identity from bytes', () => {
  const script = fs.readFileSync('tools/build-candidate.sh', 'utf8');
  assert.match(script, /h\(authorityBytes\)/);
  assert.doesNotMatch(script, /authority_manifest_digest:'sha256:/);
  assert.match(script, /fs\.writeFileSync\(path\.join\(out,'AUTHORITY-MANIFEST\.json'\),authorityBytes\)/);
});
