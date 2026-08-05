import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';

const image = process.argv[2];
if (!image || !(image.includes('@sha256:') || /^sha256:[0-9a-f]{64}$/.test(image))) throw new Error('digest-pinned image required');
const result = spawnSync('docker', ['run', '--rm', '--network', 'none', '--read-only', '--entrypoint', 'find', image, '/pack', '-type', 'f', '-perm', '/111', '-print'], { encoding: 'utf8', timeout: 30_000, env: { PATH: process.env.PATH } });
assert.equal(result.status, 0, result.stderr);
const executableFiles = result.stdout.trim().split('\n').filter(Boolean).sort();
assert.deepEqual(executableFiles, ['/pack/bin/backbond-impeccable-phase-two.mjs']);
process.stdout.write(`${JSON.stringify({ schema: 'backbond-render-image-audit-v1', image, executableFiles, findings: [] })}\n`);
