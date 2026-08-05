#!/usr/bin/env node
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { sha256 } from '../lib/policy.mjs';
import { canonicalImageIdentity } from '../lib/image_identity.mjs';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const image = process.env.BACKBOND_RENDER_IMAGE;
const outputRoot = process.env.BACKBOND_BENCHMARK_OUTPUT;
if (!image || !(image.includes('@sha256:') || /^sha256:[0-9a-f]{64}$/.test(image)) || !outputRoot || fs.existsSync(outputRoot)) throw new Error('set digest-pinned BACKBOND_RENDER_IMAGE and a new BACKBOND_BENCHMARK_OUTPUT');
fs.mkdirSync(outputRoot, { recursive: true });
const inspected = spawnSync('docker', ['image', 'inspect', image, '--format', '{{json .RepoDigests}}'], { encoding: 'utf8', timeout: 30_000, env: { PATH: process.env.PATH } });
if (inspected.status !== 0) throw new Error(`cannot inspect BACKBOND_RENDER_IMAGE: ${inspected.stderr.trim() || 'docker image inspect failed'}`);
let repoDigests;
try { repoDigests = JSON.parse(inspected.stdout); } catch { throw new Error('docker image inspect returned invalid RepoDigests'); }
const imageIdentity = canonicalImageIdentity(image, repoDigests);
const run = (mode, input, output) => {
  const result = spawnSync(process.execPath, [path.join(root, 'tools/run-sandbox.mjs'), image, mode, input, output], { encoding: 'utf8', timeout: 90_000, env: { PATH: process.env.PATH } });
  if (result.status !== 0) throw new Error(`${mode} failed: ${result.stderr}`);
};
const summary = { schema: 'backbond-phase-two-benchmark-v1', image: imageIdentity, surfaces: [] };
for (const surface of ['marketing', 'report', 'scanner']) {
  const base = path.join(root, 'benchmark', surface, 'baseline');
  const revision = path.join(root, 'benchmark', surface, 'revision');
  const target = path.join(outputRoot, surface);
  fs.mkdirSync(target);
  run('preview', base, path.join(target, 'preview'));
  const preview = JSON.parse(fs.readFileSync(path.join(target, 'preview', 'patch-preview.json')));
  assert.equal(preview.changes.length, 1, 'one bounded revision required');
  assert.equal(preview.changes[0].afterSha256, sha256(fs.readFileSync(path.join(revision, 'index.html'))));
  run('render', base, path.join(target, 'baseline'));
  run('render', revision, path.join(target, 'revision'));
  run('render', revision, path.join(target, 'repeat'));
  const auditA = fs.readFileSync(path.join(target, 'revision', 'audit.json'));
  const auditB = fs.readFileSync(path.join(target, 'repeat', 'audit.json'));
  assert.deepEqual(auditA, auditB, `${surface}: machine audit is not byte-identical`);
  const audit = JSON.parse(auditA);
  summary.surfaces.push({ surface, revisionCount: 1, auditSha256: sha256(auditA), byteIdenticalRepeat: true, automated: audit.evidence.map((entry) => ({ viewport: entry.viewport, overflow: entry.normal.overflow, zoom200Overflow: entry.zoom200.overflow, reducedMotionViolations: entry.normal.reducedMotionViolations, contrastFailures: entry.normal.contrastFailures, focus: entry.focus })) });
}
fs.writeFileSync(path.join(outputRoot, 'benchmark-summary.json'), `${JSON.stringify(summary, null, 2)}\n`, { flag: 'wx', mode: 0o600 });
