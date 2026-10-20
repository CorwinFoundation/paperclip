import test from 'node:test';
import assert from 'node:assert/strict';
import { chmod, mkdtemp, mkdir, readdir, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { captureLaunchSnapshot, verifyLaunchSnapshot } from './server-launch-snapshot.mjs';

async function fixture(t) {
  const base = await mkdtemp(path.join(tmpdir(), 'launch-test-'));
  const sourceRoot = path.join(base, 'source');
  const snapshotParent = path.join(base, 'snapshots');
  await mkdir(sourceRoot);
  t.after(async () => {
    async function writable(dir) {
      await chmod(dir, 0o700);
      for (const item of await readdir(dir, { withFileTypes: true })) if (item.isDirectory()) await writable(path.join(dir, item.name));
    }
    await writable(base); await rm(base, { recursive: true, force: true });
  });
  return { sourceRoot, snapshotParent };
}

test('previous and candidate launch bytes survive source edits and deletion', async t => {
  const options = await fixture(t);
  const script = path.join(options.sourceRoot, 'entry.cjs');
  await writeFile(script, 'console.log("previous")');
  const previous = await captureLaunchSnapshot({ ...options, inputs: ['entry.cjs'] });
  await writeFile(script, 'console.log("candidate")');
  const candidate = await captureLaunchSnapshot({ ...options, inputs: ['entry.cjs'] });
  await rm(script);
  for (const [snapshot, expected] of [[previous, 'previous'], [candidate, 'candidate']]) {
    await verifyLaunchSnapshot(snapshot);
    assert.equal(execFileSync(process.execPath, ['entry.cjs'], { cwd: snapshot.directory, encoding: 'utf8' }).trim(), expected);
    assert.ok(Object.isFrozen(snapshot.files[0]));
  }
});

test('symlinked parents and escaping paths fail and remove partial captures', async t => {
  const options = await fixture(t);
  await symlink(tmpdir(), path.join(options.sourceRoot, 'linked'));
  for (const input of ['linked/file', '../outside']) {
    await assert.rejects(captureLaunchSnapshot({ ...options, inputs: [input] }), /Symlink(?:s|ed)|Invalid/);
    assert.deepEqual(await readdir(options.snapshotParent), []);
  }
});

test('snapshot parent inside source and empty inputs are rejected', async t => {
  const options = await fixture(t);
  await assert.rejects(captureLaunchSnapshot({ ...options, snapshotParent: options.sourceRoot, inputs: ['entry'] }), /outside/);
  await assert.rejects(captureLaunchSnapshot({ ...options, inputs: [] }), /requires inputs/);
});

test('verification catches altered captured bytes', async t => {
  const options = await fixture(t);
  await writeFile(path.join(options.sourceRoot, 'entry'), 'original');
  const snapshot = await captureLaunchSnapshot({ ...options, inputs: ['entry'] });
  const file = path.join(snapshot.directory, 'entry');
  await chmod(file, 0o600); await writeFile(file, 'changed');
  await assert.rejects(verifyLaunchSnapshot(snapshot), /snapshot changed/);
});
