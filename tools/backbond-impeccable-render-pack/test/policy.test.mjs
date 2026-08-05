import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { inventory, rejectRemoteHtml } from '../lib/policy.mjs';
import { preview } from '../lib/preview.mjs';
import { sha256 } from '../lib/policy.mjs';

const fixture = () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'render-policy-'));
  fs.writeFileSync(path.join(root, 'index.html'), '<main><button>Continue</button></main>');
  return root;
};

test('inventory rejects hidden paths, symlinks, special files, and remote resources', () => {
  const root = fixture();
  fs.mkdirSync(path.join(root, '.ssh'));
  assert.throws(() => inventory(root), /hidden/);
  fs.rmSync(path.join(root, '.ssh'), { recursive: true });
  fs.symlinkSync('/etc/passwd', path.join(root, 'escape.html'));
  assert.throws(() => inventory(root), /symlink/);
  fs.unlinkSync(path.join(root, 'escape.html'));
  fs.writeFileSync(path.join(root, 'index.html'), '<img src="https://example.com/x.png">');
  assert.throws(() => rejectRemoteHtml(root), /remote/);
});

test('preview requires declared preimage, exact match, bounds, and never writes source', async () => {
  const root = fixture();
  const before = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const plan = path.join(root, 'patch-plan.json');
  fs.writeFileSync(plan, JSON.stringify({ version: 1, changes: [{ path: 'index.html', beforeSha256: sha256(before), replacements: [{ find: 'Continue', replace: 'Review evidence' }] }] }));
  const output = fs.mkdtempSync(path.join(os.tmpdir(), 'render-preview-'));
  fs.rmdirSync(output);
  await preview({ input: root, output, plan });
  assert.equal(fs.readFileSync(path.join(root, 'index.html'), 'utf8'), before);
  const result = JSON.parse(fs.readFileSync(path.join(output, 'patch-preview.json')));
  assert.equal(result.changes[0].beforeSha256, sha256(before));
  assert.notEqual(result.changes[0].afterSha256, result.changes[0].beforeSha256);
});

test('preview accepts a declared generated postimage and rejects stale or absent diffs', async () => {
  const root = fixture();
  const before = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const after = before.replace('Continue', 'Review evidence');
  const plan = path.join(root, 'patch-plan.json');
  const output = () => { const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'render-preview-')); fs.rmdirSync(dir); return dir; };
  fs.writeFileSync(plan, JSON.stringify({ version: 1, changes: [{ path: 'index.html', beforeSha256: sha256(before), afterSha256: sha256(after), replacements: [{ find: 'Continue', replace: 'Review evidence' }] }] }));
  await preview({ input: root, output: output(), plan });
  fs.writeFileSync(plan, JSON.stringify({ version: 1, changes: [{ path: 'index.html', beforeSha256: sha256(before), afterSha256: '0'.repeat(64), replacements: [{ find: 'Continue', replace: 'Review evidence' }] }] }));
  await assert.rejects(preview({ input: root, output: output(), plan }), /postimage mismatch/);
  fs.writeFileSync(plan, JSON.stringify({ version: 1, changes: [{ path: 'index.html', beforeSha256: sha256(before), afterSha256: sha256(before), replacements: [{ find: 'Continue', replace: 'Continue' }] }] }));
  await assert.rejects(preview({ input: root, output: output(), plan }), /declared diff absent/);
});
