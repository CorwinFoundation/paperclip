import fs from 'node:fs';
import path from 'node:path';

export function commitBoundedDirectory(source, destination, { byteCap, fileCap, beforeRename = () => {} }) {
  if (fs.existsSync(destination)) throw new Error('destination must not exist');
  const parent = path.dirname(path.resolve(destination));
  fs.mkdirSync(parent, { recursive: true });
  const sibling = fs.mkdtempSync(path.join(parent, `.${path.basename(destination)}.staging-`));
  let complete = false;
  let bytes = 0;
  let files = 0;
  try {
    for (const filename of fs.readdirSync(source).sort()) {
      if (!/^[a-z0-9][a-z0-9.-]*$/i.test(filename)) throw new Error('invalid output path');
      const from = path.join(source, filename);
      const stat = fs.lstatSync(from);
      if (!stat.isFile()) throw new Error('special output file');
      bytes += stat.size;
      files += 1;
      if (bytes > byteCap || files > fileCap) throw new Error('bounded output exceeded');
      fs.copyFileSync(from, path.join(sibling, filename), fs.constants.COPYFILE_EXCL);
      fs.chmodSync(path.join(sibling, filename), 0o600);
    }
    beforeRename({ source, sibling, destination, bytes, files });
    fs.renameSync(sibling, destination);
    complete = true;
    return { bytes, files };
  } finally {
    fs.rmSync(source, { recursive: true, force: true });
    if (!complete) fs.rmSync(sibling, { recursive: true, force: true });
  }
}
