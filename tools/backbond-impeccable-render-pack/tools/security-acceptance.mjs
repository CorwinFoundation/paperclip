import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const image = process.env.BACKBOND_RENDER_IMAGE;
if (!image || !(image.includes('@sha256:') || /^sha256:[0-9a-f]{64}$/.test(image))) throw new Error('digest-pinned BACKBOND_RENDER_IMAGE required');
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'backbond-render-security-'));
const runner = path.join(root, 'tools/run-sandbox.mjs');
const runId = `acceptance-${process.pid}-${Date.now()}`;
const ownedPrefix = `backbond-impeccable-render-${runId}-`;
const run = (fixture, destination) => spawnSync(process.execPath, [runner, image, 'render', fixture, destination], { encoding: 'utf8', timeout: 60_000, env: { PATH: process.env.PATH, BACKBOND_SANDBOX_RUN_ID: runId, AWS_SECRET_ACCESS_KEY: 'must-not-enter-container' } });
try {
  const remote = path.join(scratch, 'remote'); fs.mkdirSync(remote); fs.writeFileSync(path.join(remote, 'index.html'), '<main><img src="https://example.invalid/x.png"></main>');
  assert.notEqual(run(remote, path.join(scratch, 'remote-out')).status, 0, 'remote request fixture must fail closed');
  const hidden = path.join(scratch, 'hidden'); fs.mkdirSync(hidden); fs.writeFileSync(path.join(hidden, 'index.html'), '<main>safe</main>'); fs.writeFileSync(path.join(hidden, '.aws'), 'credential-path-probe');
  assert.notEqual(run(hidden, path.join(scratch, 'hidden-out')).status, 0, 'hidden credential path must fail closed');
  const symlink = path.join(scratch, 'symlink'); fs.mkdirSync(symlink); fs.writeFileSync(path.join(symlink, 'index.html'), '<main>safe</main>'); fs.symlinkSync('/etc/passwd', path.join(symlink, 'escape.html'));
  assert.notEqual(run(symlink, path.join(scratch, 'symlink-out')).status, 0, 'symlink must fail closed');
  const safe = path.join(scratch, 'safe'); fs.mkdirSync(safe); fs.writeFileSync(path.join(safe, 'index.html'), '<main><button>Safe</button></main>');
  const before = fs.readFileSync(path.join(safe, 'index.html'));
  const result = run(safe, path.join(scratch, 'safe-out'));
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(fs.readFileSync(path.join(safe, 'index.html')), before, 'read-only input changed');
  assert.ok(fs.existsSync(path.join(scratch, 'safe-out', 'audit.json')), 'bounded output missing');
  const lingering = spawnSync('docker', ['ps', '-a', '--filter', `name=${ownedPrefix}`, '--format', '{{.Names}}'], { encoding: 'utf8', env: { PATH: process.env.PATH } });
  assert.equal(lingering.stdout.trim(), '', 'sandbox cleanup left an owned container');
  const source = fs.readFileSync(runner, 'utf8');
  assert.match(source, /setTimeout\([^]*45_000\)/, 'deadline enforcement missing');
  assert.doesNotMatch(source, /AWS_SECRET_ACCESS_KEY|process\.env\.(?!PATH|BACKBOND_SANDBOX_RUN_ID)/, 'secret inheritance path present');
  process.stdout.write(`${JSON.stringify({ schema: 'backbond-render-security-acceptance-v1', image, checks: ['network-denial', 'secret-stripping', 'credential-path-denial', 'read-only-input', 'bounded-output', 'deadline-configured', 'cleanup', 'symlink-denial', 'publication-mode-denied'] })}\n`);
} finally {
  fs.rmSync(scratch, { recursive: true, force: true });
}
