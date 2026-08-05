import assert from 'node:assert/strict';
import test from 'node:test';
import { canonicalImageIdentity } from '../lib/image_identity.mjs';

const manifest = 'sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';
const repoDigests = [`registry.example/backbond-phase2@${manifest}`];

test('canonicalizes equivalent digest-pinned image reference spellings', () => {
  assert.equal(canonicalImageIdentity(`registry.example/backbond-phase2@${manifest}`, repoDigests), manifest);
  assert.equal(canonicalImageIdentity(manifest, repoDigests), manifest);
});

test('rejects an ambiguous image identity', () => {
  const other = 'sha256:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789';
  assert.throws(() => canonicalImageIdentity(manifest, [`one@${manifest}`, `two@${other}`]), /exactly one OCI manifest digest/);
});
