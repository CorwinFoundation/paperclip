import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, symlink, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { selectServerLaunchInputs } from './server-launch-inputs.mjs';
async function fixture(t) {
  const root = await mkdtemp(path.join(tmpdir(), 'launch-inputs-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  for (const dir of ['server/src', 'server/node_modules/tsx', 'server/ui-dist', 'packages/shared/src', 'server/node_modules/@paperclipai']) await mkdir(path.join(root, dir), { recursive: true });
  for (const file of ['package.json', 'tsconfig.base.json', 'server/tsconfig.json']) await writeFile(path.join(root, file), '{}');
  await writeFile(path.join(root, 'server/package.json'), JSON.stringify({ dependencies: { '@paperclipai/shared': 'workspace:*' } }));
  await writeFile(path.join(root, 'packages/shared/package.json'), '{}');
  await symlink('../../../packages/shared', path.join(root, 'server/node_modules/@paperclipai/shared'));
  return root;
}
test('selects loader, workspace source and static assets without instance or environment files', async t => {
  const root = await fixture(t);
  const inputs = await selectServerLaunchInputs(root, { uiMode: 'static' });
  for (const required of ['server/src', 'server/ui-dist', 'server/node_modules/tsx', 'packages/shared']) assert.ok(inputs.includes(required));
  assert.ok(!inputs.includes('.env'));
  assert.ok(!inputs.includes('.paperclip'));
});
test('rejects missing installed loader before capture or child stop', async t => {
  const root = await fixture(t);
  await rm(path.join(root, 'server/node_modules/tsx'), { recursive: true });
  await assert.rejects(selectServerLaunchInputs(root, { uiMode: 'none' }), /Missing.*tsx/);
});
test('rejects shared-checkout dependencies', async t => {
  const root = await fixture(t);
  await rm(path.join(root, 'server/node_modules/tsx'), { recursive: true });
  await symlink(tmpdir(), path.join(root, 'server/node_modules/tsx'));
  await assert.rejects(selectServerLaunchInputs(root, { uiMode: 'none' }), /escapes source root.*tsx/);
});
test('requires UI inputs when Vite middleware is enabled', async t => {
  const root = await fixture(t);
  await assert.rejects(selectServerLaunchInputs(root), /Missing.*ui\/src/);
});
