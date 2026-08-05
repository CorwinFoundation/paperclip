import fs from 'node:fs';
import path from 'node:path';
import { inventory, LIMITS, safeRelative, sha256 } from './policy.mjs';

export async function preview({ input, output, plan }) {
  const manifest = inventory(input);
  const parsed = JSON.parse(fs.readFileSync(plan, 'utf8'));
  if (parsed.version !== 1 || !Array.isArray(parsed.changes) || parsed.changes.length < 1 || parsed.changes.length > LIMITS.patchFiles) throw new Error('invalid patch plan');
  let patchBytes = 0;
  const previews = [];
  fs.mkdirSync(output, { recursive: true });
  for (const change of parsed.changes) {
    const relative = safeRelative(change.path);
    const sourcePath = path.join(input, relative);
    const before = fs.readFileSync(sourcePath, 'utf8');
    if (sha256(before) !== change.beforeSha256) throw new Error(`preimage mismatch: ${relative}`);
    if (!Array.isArray(change.replacements) || change.replacements.length < 1) throw new Error(`empty replacements: ${relative}`);
    let after = before;
    for (const replacement of change.replacements) {
      if (typeof replacement.find !== 'string' || !replacement.find || typeof replacement.replace !== 'string') throw new Error('invalid replacement');
      const first = after.indexOf(replacement.find);
      if (first < 0 || first !== after.lastIndexOf(replacement.find)) throw new Error(`replacement must match exactly once: ${relative}`);
      patchBytes += Buffer.byteLength(replacement.find) + Buffer.byteLength(replacement.replace);
      if (patchBytes > LIMITS.patchBytes) throw new Error('patch byte limit exceeded');
      after = after.replace(replacement.find, replacement.replace);
    }
    const afterSha256 = sha256(after);
    if (afterSha256 === sha256(before)) throw new Error(`declared diff absent: ${relative}`);
    if (change.afterSha256 !== undefined && afterSha256 !== change.afterSha256) throw new Error(`postimage mismatch: ${relative}`);
    previews.push({ path: relative, beforeSha256: sha256(before), afterSha256, beforeBytes: Buffer.byteLength(before), afterBytes: Buffer.byteLength(after), preview: `--- a/${relative}\n+++ b/${relative}\n@@ bounded replacement @@\n${change.replacements.map((r) => `-${r.find}\n+${r.replace}`).join('\n')}\n` });
  }
  fs.writeFileSync(path.join(output, 'patch-preview.json'), `${JSON.stringify({ schema: 'backbond-patch-preview-v1', files: manifest.length, patchBytes, changes: previews }, null, 2)}\n`, { mode: 0o600, flag: 'wx' });
}
