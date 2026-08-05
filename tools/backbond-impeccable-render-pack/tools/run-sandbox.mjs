#!/usr/bin/env node
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { commitBoundedDirectory } from './atomic-output.mjs';
import { dockerCreateArgs, OUTPUT_CAP_BYTES } from './sandbox-command.mjs';

const [image, mode, inputDir, destination] = process.argv.slice(2);
if (!image || !(image.includes('@sha256:') || /^sha256:[0-9a-f]{64}$/.test(image)) || !mode || !inputDir || !destination) {
  process.stderr.write('usage: run-sandbox.mjs <image@sha256> <render|preview> <staged-input> <new-output-dir>\n');
  process.exit(64);
}
if (fs.existsSync(destination)) throw new Error('destination must not exist');
const runId = process.env.BACKBOND_SANDBOX_RUN_ID || crypto.randomUUID();
if (!/^[a-zA-Z0-9_.-]{1,64}$/.test(runId)) throw new Error('invalid sandbox run id');
const name = `backbond-impeccable-render-${runId}-${crypto.randomBytes(8).toString('hex')}`;
const stagedOutput = fs.mkdtempSync(path.join(os.tmpdir(), 'backbond-impeccable-render-output-'));
const env = { PATH: process.env.PATH };
let complete = false;
let created = false;
let attachedChild;
const removeOwnedContainer = () => {
  if (!created) return;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    spawnSync('docker', ['rm', '--force', name], { stdio: 'ignore', env });
    const inspect = spawnSync('docker', ['container', 'inspect', name], { stdio: 'ignore', env });
    if (inspect.status !== 0) { created = false; return; }
  }
  throw new Error(`failed to remove owned sandbox container: ${name}`);
};
const signalExitCode = { SIGHUP: 129, SIGINT: 130, SIGTERM: 143 };
for (const signal of Object.keys(signalExitCode)) {
  process.once(signal, () => {
    try {
      attachedChild?.kill('SIGKILL');
      removeOwnedContainer();
    } finally {
      process.exit(signalExitCode[signal]);
    }
  });
}
const attachOwnedContainer = () => new Promise((resolve, reject) => {
  const child = spawn('docker', ['start', '--attach', name], { env, stdio: ['ignore', 'pipe', 'pipe'] });
  attachedChild = child;
  const stdout = [];
  const stderr = [];
  let stdoutBytes = 0;
  let stderrBytes = 0;
  let timedOut = false;
  const deadline = setTimeout(() => { timedOut = true; child.kill('SIGKILL'); }, 45_000);
  child.stdout.on('data', chunk => {
    stdoutBytes += chunk.length;
    if (stdoutBytes > 90 * 1024 * 1024) child.kill('SIGKILL');
    else stdout.push(chunk);
  });
  child.stderr.on('data', chunk => {
    stderrBytes += chunk.length;
    if (stderrBytes > 2 * 1024 * 1024) child.kill('SIGKILL');
    else stderr.push(chunk);
  });
  child.once('error', reject);
  child.once('close', status => {
    clearTimeout(deadline);
    attachedChild = undefined;
    resolve({ status, stdout: Buffer.concat(stdout).toString('utf8'), stderr: Buffer.concat(stderr).toString('utf8'), timedOut, boundedOutputExceeded: stdoutBytes > 90 * 1024 * 1024 || stderrBytes > 2 * 1024 * 1024 });
  });
});
try {
  let result = spawnSync('docker', dockerCreateArgs({ image, inputDir, mode, name }), { encoding: 'utf8', timeout: 60_000, env, maxBuffer: 2 * 1024 * 1024 });
  if (result.status !== 0) throw new Error(`docker create failed: ${result.stderr.trim()}`);
  created = true;
  result = await attachOwnedContainer();
  if (result.timedOut) throw new Error('sandbox deadline exceeded');
  if (result.boundedOutputExceeded) throw new Error('sandbox output exceeded');
  if (result.status !== 0) throw new Error(`sandbox failed: ${result.stderr.trim()}`);
  const envelope = JSON.parse(result.stdout);
  if (envelope.schema !== 'backbond-bounded-output-v1' || !envelope.files || Array.isArray(envelope.files)) throw new Error('invalid bounded output');
  let bytes = 0;
  let files = 0;
  for (const filename of Object.keys(envelope.files).sort()) {
    if (!/^[a-z0-9][a-z0-9.-]*$/i.test(filename)) throw new Error('invalid output path');
    const content = Buffer.from(envelope.files[filename], 'base64');
    bytes += content.length;
    files += 1;
    if (bytes > OUTPUT_CAP_BYTES || files > 32) throw new Error('bounded output exceeded');
    fs.writeFileSync(path.join(stagedOutput, filename), content, { flag: 'wx', mode: 0o600 });
  }
  commitBoundedDirectory(stagedOutput, destination, { byteCap: OUTPUT_CAP_BYTES, fileCap: 32 });
  complete = true;
} finally {
  removeOwnedContainer();
  if (!complete) fs.rmSync(stagedOutput, { recursive: true, force: true });
}
