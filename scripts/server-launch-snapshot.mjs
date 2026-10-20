import { createHash } from 'node:crypto';
import { chmod, lstat, mkdir, mkdtemp, readFile, readdir, readlink, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import path from 'node:path';

// Capture an explicit launch input set. Runtime data and environment secrets do
// not belong in this directory. Callers must include the full dependency closure.
export async function captureLaunchSnapshot({ sourceRoot, snapshotParent, inputs, captureDependencyLinks = false }) {
  const root = path.resolve(sourceRoot);
  const parent = path.resolve(snapshotParent);
  if (parent === root || parent.startsWith(root + path.sep)) {
    throw Error('Snapshot parent must be outside the source tree');
  }
  if (!inputs.length) throw Error('Launch snapshot requires inputs');
  await mkdir(parent, { recursive: true, mode: 0o700 });
  const directory = await mkdtemp(path.join(parent, 'launch-'));
  const files = [];
  const links = [];
  const visited = new Set();
  const directories = new Set([directory]);
  try {
    async function capture(relativePath) {
      const absolute = path.resolve(root, relativePath);
      if (absolute === root || !absolute.startsWith(root + path.sep)) throw Error('Invalid launch input path');
      if (visited.has(absolute)) return;
      visited.add(absolute);
      let parentComponent = root;
      for (const part of path.relative(root, path.dirname(absolute)).split(path.sep).filter(Boolean)) {
        parentComponent = path.join(parentComponent, part);
        if ((await lstat(parentComponent)).isSymbolicLink()) throw Error('Symlinked launch input parent');
      }
      const stats = await lstat(absolute);
      if (stats.isSymbolicLink() && captureDependencyLinks) {
        const target = await realpath(absolute);
        if (!target.startsWith(root + path.sep)) throw Error('Dependency link escapes source root');
        const relative = path.relative(root, absolute);
        const targetRelative = path.relative(root, target);
        const destination = path.join(directory, relative);
        await mkdir(path.dirname(destination), { recursive: true, mode: 0o700 });
        let ancestor = path.dirname(destination);
        while (ancestor !== directory) { directories.add(ancestor); ancestor = path.dirname(ancestor); }
        const linkTarget = path.relative(path.dirname(destination), path.join(directory, targetRelative));
        await symlink(linkTarget, destination);
        links.push(Object.freeze({ path: relative, target: linkTarget }));
        await capture(targetRelative);
        return;
      }
      // Check each component: a parent symlink could otherwise escape the root.
      const relative = path.relative(root, absolute);
      let component = root;
      for (const part of relative.split(path.sep)) {
        component = path.join(component, part);
        if ((await lstat(component)).isSymbolicLink()) throw Error('Symlinks are not launch snapshot inputs');
      }
      const destination = path.join(directory, relative);
      if (stats.isDirectory()) {
        await mkdir(destination, { recursive: true, mode: 0o700 });
        directories.add(destination);
        for (const name of (await readdir(absolute)).sort()) await capture(path.join(relative, name));
      } else if (stats.isFile()) {
        if (files.some(file => file.path === relative)) return;
        const bytes = await readFile(absolute);
        await mkdir(path.dirname(destination), { recursive: true, mode: 0o700 });
        let ancestor = path.dirname(destination);
        while (ancestor !== directory) { directories.add(ancestor); ancestor = path.dirname(ancestor); }
        await writeFile(destination, bytes, { flag: 'wx', mode: stats.mode & 0o111 ? 0o500 : 0o400 });
        files.push(Object.freeze({ path: relative, sha256: createHash('sha256').update(bytes).digest('hex') }));
      } else throw Error('Unsupported launch snapshot input');
    }
    for (const input of inputs) await capture(input);
    files.sort((a, b) => a.path.localeCompare(b.path));
    if (!files.length) throw Error('Launch snapshot contains no files');
    for (const dir of [...directories].sort((a, b) => b.length - a.length)) await chmod(dir, 0o500);
    return Object.freeze({ directory, directories: Object.freeze([...directories].map(dir => path.relative(directory, dir))), files: Object.freeze(files), links: Object.freeze(links) });
  } catch (error) {
    for (const dir of directories) await chmod(dir, 0o700).catch(() => {});
    await rm(directory, { recursive: true, force: true });
    throw error;
  }
}

export async function verifyLaunchSnapshot(snapshot) {
  for (const directory of snapshot.directories ?? ['']) {
    if (!(await lstat(path.join(snapshot.directory, directory))).isDirectory()) {
      throw Error(`Launch snapshot directory replaced: ${directory}`);
    }
  }
  for (const link of snapshot.links ?? []) {
    if (await readlink(path.join(snapshot.directory, link.path)) !== link.target) {
      throw Error(`Launch snapshot link changed: ${link.path}`);
    }
  }
  for (const file of snapshot.files) {
    if (!(await lstat(path.join(snapshot.directory, file.path))).isFile()) {
      throw Error(`Launch snapshot file replaced: ${file.path}`);
    }
    const bytes = await readFile(path.join(snapshot.directory, file.path));
    if (createHash('sha256').update(bytes).digest('hex') !== file.sha256) {
      throw Error(`Launch snapshot changed: ${file.path}`);
    }
  }
}
