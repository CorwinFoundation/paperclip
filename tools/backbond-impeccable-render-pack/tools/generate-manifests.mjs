import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { sha256 } from '../lib/policy.mjs';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const excluded = new Set(['.git', 'BUILD-MANIFEST.json', 'SOURCE-MANIFEST.json', 'node_modules', 'build-a', 'build-b', 'evidence', 'blind-packet']);
const files = [];
function visit(dir, prefix = '') {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    if (excluded.has(entry.name)) continue;
    const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
    const absolute = path.join(dir, entry.name);
    if (entry.isDirectory()) visit(absolute, relative);
    else if (entry.isFile()) {
      if (relative.startsWith('archive-evidence-') || relative.startsWith('beaaa-20854-final')) continue;
      files.push({ path: relative, bytes: fs.statSync(absolute).size, sha256: sha256(fs.readFileSync(absolute)) });
    }
    else throw new Error(`special source file: ${relative}`);
  }
}
visit(root);
const source = { schema: 'backbond-render-source-manifest-v1', upstream: { repository: 'pbakaus/impeccable', commit: '33b9a3752b8852c7adb7f4935a3f6c160bf3fefc', version: '4.0.4' }, files };
fs.writeFileSync(path.join(root, 'SOURCE-MANIFEST.json'), `${JSON.stringify(source, null, 2)}\n`);
const build = { schema: 'backbond-render-build-manifest-v1', phaseOneBaseGit: '497e3b80a5744fc3a5fae91749e229c6bf52da2f', runtimeBase: 'mcr.microsoft.com/playwright@sha256:b27e719ecbfef153e13fd24e8341736733bf2658b229677eb21ff57ff5d7fb29', node: '22.18.0', playwright: '1.55.0', chromium: '140.0.7339.16', lockfileSha256: sha256(fs.readFileSync(path.join(root, 'package-lock.json'))), sourceManifestSha256: sha256(fs.readFileSync(path.join(root, 'SOURCE-MANIFEST.json'))), candidateGit: 'recorded from immutable commit after manifest generation', candidateOci: 'recorded from the final local image after immutable commit' };
fs.writeFileSync(path.join(root, 'BUILD-MANIFEST.json'), `${JSON.stringify(build, null, 2)}\n`);
