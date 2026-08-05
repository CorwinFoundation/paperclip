import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

export const LIMITS = Object.freeze({ files: 128, fileBytes: 2 * 1024 * 1024, inputBytes: 16 * 1024 * 1024, patchFiles: 8, patchBytes: 64 * 1024 });
export const ALLOWED = new Set(['.html', '.css', '.png', '.jpg', '.jpeg', '.svg', '.woff', '.woff2', '.json']);
export const sha256 = (value) => crypto.createHash('sha256').update(value).digest('hex');
export const stable = (value) => JSON.stringify(value, Object.keys(value).sort(), 2) + '\n';

export function safeRelative(value) {
  if (typeof value !== 'string' || !value || path.isAbsolute(value) || value.includes('\\')) throw new Error('invalid relative path');
  const normalized = path.posix.normalize(value);
  if (normalized !== value || normalized.startsWith('../') || normalized.split('/').some((part) => !part || part.startsWith('.'))) throw new Error('hidden or traversing path denied');
  return normalized;
}

export function inventory(root) {
  const result = [];
  let bytes = 0;
  function visit(dir, prefix = '') {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      if (entry.name.startsWith('.')) throw new Error('hidden path denied');
      const relative = safeRelative(prefix ? `${prefix}/${entry.name}` : entry.name);
      const absolute = path.join(root, relative);
      const stat = fs.lstatSync(absolute);
      if (stat.isSymbolicLink() || (!stat.isDirectory() && !stat.isFile())) throw new Error('symlink or special file denied');
      if (stat.isDirectory()) visit(absolute, relative);
      else {
        if (!ALLOWED.has(path.extname(entry.name).toLowerCase())) throw new Error(`unsupported file: ${relative}`);
        if (stat.size > LIMITS.fileBytes) throw new Error(`file limit exceeded: ${relative}`);
        bytes += stat.size;
        if (bytes > LIMITS.inputBytes || result.length >= LIMITS.files) throw new Error('input bounds exceeded');
        result.push({ path: relative, bytes: stat.size, sha256: sha256(fs.readFileSync(absolute)) });
      }
    }
  }
  visit(root);
  if (!result.some((file) => file.path === 'index.html')) throw new Error('index.html required');
  return result;
}

export function rejectRemoteHtml(root) {
  for (const file of inventory(root).filter((item) => item.path.endsWith('.html') || item.path.endsWith('.css'))) {
    const source = fs.readFileSync(path.join(root, file.path), 'utf8');
    if (/(?:https?:|wss?:|ftp:|data:text\/html|javascript:)/i.test(source)) throw new Error(`remote or executable URL denied: ${file.path}`);
    if (/<script\b/i.test(source)) throw new Error(`scripts denied: ${file.path}`);
  }
}
