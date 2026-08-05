import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const runner = fs.readFileSync(path.join(root, 'tools/run-sandbox.mjs'), 'utf8');
const acceptance = fs.readFileSync(path.join(root, 'tools/security-acceptance.mjs'), 'utf8');

assert.match(runner, /BACKBOND_SANDBOX_RUN_ID/, 'runner must accept a caller-owned run id');
assert.match(runner, /container', 'inspect'/, 'cleanup must await confirmed container removal');
assert.match(runner, /spawn\('docker', \['start', '--attach'/, 'attached lifecycle must be interruptible');
assert.match(acceptance, /ownedPrefix/, 'acceptance must scope leak checks to the current run');
assert.doesNotMatch(acceptance, /filter', 'name=backbond-impeccable-render-'/, 'global-prefix leak checks race with concurrent builds');

const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'backbond-signal-cleanup-'));
const bin = path.join(scratch, 'bin');
const state = path.join(scratch, 'state');
const input = path.join(scratch, 'input');
fs.mkdirSync(bin); fs.mkdirSync(state); fs.mkdirSync(input);
fs.writeFileSync(path.join(input, 'index.html'), '<main>signal cleanup</main>');
const docker = path.join(bin, 'docker');
fs.writeFileSync(docker, `#!/bin/sh
set -eu
state=${state}
case "$1" in
  create)
    while [ "$1" != "--name" ]; do shift; done; name=$2
    : > "$state/$name" ;;
  start)
    sleep 60 ;;
  rm)
    name=$3; rm -f "$state/$name" ;;
  container)
    name=$3; test -e "$state/$name" ;;
  *) exit 64 ;;
esac
`);
fs.chmodSync(docker, 0o755);

const waitFor = async predicate => {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  throw new Error('timed out waiting for fake container state');
};
for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP']) {
  const runId = `signal-${signal.toLowerCase()}`;
  const destination = path.join(scratch, `output-${signal}`);
  const child = spawn(process.execPath, [path.join(root, 'tools/run-sandbox.mjs'), `sha256:${'a'.repeat(64)}`, 'render', input, destination], {
    env: { PATH: `${bin}:${process.env.PATH}`, BACKBOND_FAKE_DOCKER_STATE: state, BACKBOND_SANDBOX_RUN_ID: runId },
    stdio: 'ignore',
  });
  await waitFor(() => fs.readdirSync(state).some(name => name.startsWith(`backbond-impeccable-render-${runId}-`)));
  child.kill(signal);
  const status = await new Promise(resolve => child.once('exit', code => resolve(code)));
  assert.equal(status, { SIGINT: 130, SIGTERM: 143, SIGHUP: 129 }[signal], `${signal} exit status`);
  assert.equal(fs.readdirSync(state).filter(name => name.startsWith(`backbond-impeccable-render-${runId}-`)).length, 0, `${signal} left its owned attached container`);
}
fs.rmSync(scratch, { recursive: true, force: true });

process.stdout.write('sandbox cleanup ownership regression: ok\n');
