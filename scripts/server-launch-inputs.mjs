import { lstat, readFile, readdir, realpath } from 'node:fs/promises';
import path from 'node:path';

// The dev entry uses tsx, source exports, migration/onboarding assets, and Vite.
// Never copy .env, instance state, or the whole repository as a launch input.
export async function selectServerLaunchInputs(sourceRoot, { uiMode = 'vite-dev' } = {}) {
  const root = await realpath(sourceRoot);
  if (!['vite-dev', 'static', 'none'].includes(uiMode)) throw Error('Unknown launch UI mode');
  const inputs = new Set(['package.json', 'tsconfig.base.json', 'server/package.json', 'server/tsconfig.json', 'server/src']);
  const packages = new Set();
  async function requireInput(relative) {
    const absolute = path.join(root, relative);
    let resolved;
    try { resolved = await realpath(absolute); }
    catch { throw Error(`Missing server launch input: ${relative}`); }
    if (!resolved.startsWith(root + path.sep)) throw Error(`Server launch input escapes source root: ${relative}`);
    inputs.add(relative);
    return absolute;
  }
  const dependencyDirectories = new Set();
  async function installedInput(relative) {
    const installed = await requireInput(relative);
    const targetRelative = path.relative(root, await realpath(installed));
    // Loader tools also resolve dependencies from their pnpm sibling directory.
    const parts = targetRelative.split(path.sep);
    const virtualStore = parts.indexOf('.pnpm');
    if (virtualStore >= 0) {
      const siblings = parts.slice(0, virtualStore + 3).join(path.sep);
      if (!dependencyDirectories.has(siblings)) {
        dependencyDirectories.add(siblings);
        await requireInput(siblings);
        async function followLinks(directory) {
          for (const entry of await readdir(path.join(root, directory), { withFileTypes: true })) {
            const child = path.join(directory, entry.name);
            if (entry.isSymbolicLink()) await installedInput(child);
            else if (entry.isDirectory() && entry.name.startsWith('@')) await followLinks(child);
          }
        }
        await followLinks(siblings);
      }
    }
    return installed;
  }
  async function dependencies(packageDirectory) {
    if (packages.has(packageDirectory)) return;
    packages.add(packageDirectory);
    const manifest = JSON.parse(await readFile(await requireInput(path.join(packageDirectory, 'package.json')), 'utf8'));
    // Include package contents: source exports, compiled SDK exports, and assets.
    if (packageDirectory !== 'server' && packageDirectory !== 'ui') await requireInput(packageDirectory);
    for (const name of Object.keys(manifest.dependencies ?? {})) {
      const relative = path.join(packageDirectory, 'node_modules', name);
      const installed = await installedInput(relative);
      const target = await realpath(installed);
      const targetRelative = path.relative(root, target);
      if (!targetRelative.split(path.sep).includes('node_modules')) await dependencies(targetRelative);

    }
  }
  await dependencies('server');
  // Select actual loader bytes rather than a shell wrapper with a source path.
  await installedInput('server/node_modules/tsx');
  if (uiMode === 'vite-dev') {
    for (const relative of ['ui/src', 'ui/public', 'ui/index.html', 'ui/vite.config.ts', 'ui/tsconfig.json', 'server/node_modules/vite']) await requireInput(relative);
    await dependencies('ui');
  } else if (uiMode === 'static') {
    await requireInput('server/ui-dist');
  }
  for (const relative of inputs) await requireInput(relative);
  for (const relative of ['server/src', ...(uiMode === 'static' ? ['server/ui-dist'] : [])]) {
    if (!(await lstat(path.join(root, relative))).isDirectory()) throw Error(`Launch asset root must be a directory: ${relative}`);
  }
  return Object.freeze([...inputs].sort());
}
