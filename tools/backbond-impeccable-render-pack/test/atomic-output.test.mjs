import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { commitBoundedDirectory } from '../tools/atomic-output.mjs';

const makeRoot = () => fs.mkdtempSync(path.join(os.tmpdir(), 'backbond-atomic-output-test-'));

test('copies a bounded source into a same-filesystem sibling and atomically renames it', () => {
  const root = makeRoot();
  const source = fs.mkdtempSync(path.join(os.tmpdir(), 'backbond-cross-device-source-'));
  const destination = path.join(root, 'final');
  fs.writeFileSync(path.join(source, 'audit.json'), '{"pass":true}\n');
  let observed = false;
  try {
    const result = commitBoundedDirectory(source, destination, { byteCap: 1024, fileCap: 2, beforeRename: ({ sibling }) => {
      observed = true;
      assert.equal(fs.existsSync(destination), false, 'destination became visible before rename');
      assert.equal(fs.statSync(sibling).dev, fs.statSync(root).dev, 'staging sibling is not on destination filesystem');
      assert.equal(fs.readFileSync(path.join(sibling, 'audit.json'), 'utf8'), '{"pass":true}\n');
    } });
    assert.deepEqual(result, { bytes: 14, files: 1 });
    assert.equal(observed, true);
    assert.equal(fs.existsSync(source), false, 'cross-filesystem source was not cleaned');
    assert.equal(fs.readFileSync(path.join(destination, 'audit.json'), 'utf8'), '{"pass":true}\n');
    assert.deepEqual(fs.readdirSync(root), ['final'], 'staging sibling remains after commit');
  } finally { fs.rmSync(root, { recursive: true, force: true }); fs.rmSync(source, { recursive: true, force: true }); }
});

test('cleans source and sibling unconditionally when a cap rejects the copy', () => {
  const root = makeRoot();
  const source = fs.mkdtempSync(path.join(os.tmpdir(), 'backbond-cross-device-source-'));
  const destination = path.join(root, 'final');
  fs.writeFileSync(path.join(source, 'large.bin'), Buffer.alloc(32));
  try {
    assert.throws(() => commitBoundedDirectory(source, destination, { byteCap: 16, fileCap: 1 }), /bounded output exceeded/);
    assert.equal(fs.existsSync(source), false, 'rejected source was not cleaned');
    assert.equal(fs.existsSync(destination), false, 'rejected destination exists');
    assert.deepEqual(fs.readdirSync(root), [], 'rejected staging sibling remains');
  } finally { fs.rmSync(root, { recursive: true, force: true }); fs.rmSync(source, { recursive: true, force: true }); }
});

test('cleans source and sibling when the pre-rename boundary fails', () => {
  const root = makeRoot();
  const source = fs.mkdtempSync(path.join(os.tmpdir(), 'backbond-cross-device-source-'));
  const destination = path.join(root, 'final');
  fs.writeFileSync(path.join(source, 'audit.json'), '{}\n');
  try {
    assert.throws(() => commitBoundedDirectory(source, destination, { byteCap: 16, fileCap: 1, beforeRename: () => { throw new Error('injected boundary failure'); } }), /injected boundary failure/);
    assert.equal(fs.existsSync(source), false);
    assert.equal(fs.existsSync(destination), false);
    assert.deepEqual(fs.readdirSync(root), []);
  } finally { fs.rmSync(root, { recursive: true, force: true }); fs.rmSync(source, { recursive: true, force: true }); }
});
