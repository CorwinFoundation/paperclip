import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const enabled = ['bin', 'lib'];
for (const dir of enabled) {
  for (const name of fs.readdirSync(path.join(root, dir), { recursive: true })) {
    const file = path.join(root, dir, name);
    if (!fs.statSync(file).isFile()) continue;
    const source = fs.readFileSync(file, 'utf8');
    assert.doesNotMatch(source, /node:(?:child_process|net|tls|http|https|dgram|vm|module)/, `${name}: forbidden runtime API`);
    assert.doesNotMatch(source, /\b(?:eval|Function|WebSocket)\s*\(/, `${name}: dynamic execution or socket API`);
    assert.doesNotMatch(source, /import\s*\(/, `${name}: dynamic import`);
  }
}
process.stdout.write(JSON.stringify({ schema: 'backbond-render-pack-audit-v1', enabled, entrypoints: ['bin/backbond-impeccable-phase-two.mjs'], findings: [] }) + '\n');
