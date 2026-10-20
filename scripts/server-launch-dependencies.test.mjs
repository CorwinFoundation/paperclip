import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, symlink, rm, chmod, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { captureLaunchSnapshot, verifyLaunchSnapshot } from './server-launch-snapshot.mjs';

async function fixture(t) {
  const base = await mkdtemp(path.join(tmpdir(), 'dependency-capture-'));
  const sourceRoot = path.join(base, 'source');
  await mkdir(path.join(sourceRoot, 'node_modules'), { recursive: true });
  t.after(async () => {
    async function writable(dir) {
      await chmod(dir, 0o700);
      for (const item of await readdir(dir, { withFileTypes: true })) {
        if (item.isDirectory()) await writable(path.join(dir, item.name));
      }
    }
    await writable(base); await rm(base, { recursive: true, force: true });
  });
  return { sourceRoot, snapshotParent: path.join(base, 'snapshots'), captureDependencyLinks: true };
}

test('copies linked transitive dependency targets and runs after source removal', async t => {
  const options = await fixture(t);
  const root = options.sourceRoot;
  await mkdir(path.join(root, 'store/dep/node_modules'), { recursive: true });
  await mkdir(path.join(root, 'packages/shared'), { recursive: true });
  await writeFile(path.join(root, 'entry.cjs'), 'console.log(require("dep"))');
  await writeFile(path.join(root, 'store/dep/index.js'), 'module.exports = require("shared")');
  await writeFile(path.join(root, 'packages/shared/index.js'), 'module.exports = "captured"');
  await symlink('../store/dep', path.join(root, 'node_modules/dep'));
  await symlink('../../../packages/shared', path.join(root, 'store/dep/node_modules/shared'));
  const snapshot = await captureLaunchSnapshot({ ...options, inputs: ['entry.cjs', 'node_modules'] });
  await rm(root, { recursive: true });
  await verifyLaunchSnapshot(snapshot);
  assert.equal(execFileSync(process.execPath, ['entry.cjs'], { cwd: snapshot.directory, encoding: 'utf8' }).trim(), 'captured');
  assert.equal(snapshot.links.length, 2);
});

test('rejects dependencies outside source and removes partial snapshot', async t => {
  const options = await fixture(t);
  await symlink(tmpdir(), path.join(options.sourceRoot, 'node_modules/outside'));
  await assert.rejects(captureLaunchSnapshot({ ...options, inputs: ['node_modules'] }), /escapes source/);
  assert.deepEqual(await readdir(options.snapshotParent), []);
});

test('verification rejects changed dependency links', async t => {
  const options = await fixture(t);
  await writeFile(path.join(options.sourceRoot, 'dep.cjs'), 'module.exports=1');
  await symlink('../dep.cjs', path.join(options.sourceRoot, 'node_modules/dep'));
  const snapshot = await captureLaunchSnapshot({ ...options, inputs: ['node_modules'] });
  const modules = path.join(snapshot.directory, 'node_modules');
  await chmod(modules, 0o700);
  await rm(path.join(modules, 'dep'));
  await symlink('../other.cjs', path.join(modules, 'dep'));
  await assert.rejects(verifyLaunchSnapshot(snapshot), /link changed/);
});

test('verification rejects a replaced dependency directory', async t => {
  const options = await fixture(t);
  await writeFile(path.join(options.sourceRoot, 'node_modules/dep.cjs'), 'module.exports=1');
  const snapshot = await captureLaunchSnapshot({ ...options, inputs: ['node_modules'] });
  await chmod(snapshot.directory, 0o700);
  await chmod(path.join(snapshot.directory, 'node_modules'), 0o700);
  await rm(path.join(snapshot.directory, 'node_modules'), { recursive: true });
  await symlink(path.join(options.sourceRoot, 'node_modules'), path.join(snapshot.directory, 'node_modules'));
  await assert.rejects(verifyLaunchSnapshot(snapshot), /directory replaced/);
});
