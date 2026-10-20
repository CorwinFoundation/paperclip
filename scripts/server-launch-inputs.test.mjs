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

test('selects pnpm loader dependency siblings such as esbuild', async t => {
  const root = await fixture(t);
  await rm(path.join(root, 'server/node_modules/tsx'), { recursive: true });
  const loaderStore = 'node_modules/.pnpm/tsx@fixture/node_modules';
  await mkdir(path.join(root, loaderStore, 'tsx'), { recursive: true });
  await mkdir(path.join(root, 'node_modules/.pnpm/esbuild@fixture/node_modules/esbuild'), { recursive: true });
  await symlink('../../esbuild@fixture/node_modules/esbuild', path.join(root, loaderStore, 'esbuild'));
  await symlink('../../' + loaderStore + '/tsx', path.join(root, 'server/node_modules/tsx'));
  const esbuildStore = 'node_modules/.pnpm/esbuild@fixture/node_modules';
  const binaryStore = 'node_modules/.pnpm/@esbuild+linux-x64@fixture/node_modules/@esbuild/linux-x64';
  await mkdir(path.join(root, esbuildStore, '@esbuild'), { recursive: true });
  await mkdir(path.join(root, binaryStore), { recursive: true });
  await symlink(path.relative(path.join(root, esbuildStore, '@esbuild'), path.join(root, binaryStore)), path.join(root, esbuildStore, '@esbuild/linux-x64'));
  const inputs = await selectServerLaunchInputs(root, { uiMode: 'none' });
  assert.ok(inputs.includes(esbuildStore), 'transitive native dependency directory must be captured');
  assert.ok(inputs.includes('node_modules/.pnpm/@esbuild+linux-x64@fixture/node_modules'), 'native binary dependency directory must be selected');
  assert.ok(inputs.includes(loaderStore), 'loader sibling dependencies must be captured');
});
