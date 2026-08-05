#!/usr/bin/env node
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { sha256 } from '../lib/policy.mjs';
import { normalizePdfMetadata } from './pdf-normalization.mjs';
const root = path.resolve(process.argv[2] || 'blind-packet');
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'packet-manifest.json')));
const rawPdfManifest = JSON.parse(fs.readFileSync(path.join(root, 'pdf-raw-hashes.json')));
const rawByPath = new Map(rawPdfManifest.files.map((entry) => [entry.path, entry]));
for (const entry of manifest.files) {
  const content = fs.readFileSync(path.join(root, entry.path));
  if (entry.path.endsWith('.pdf')) {
    const normalized = normalizePdfMetadata(content);
    assert.equal(normalized.normalizedSha256, entry.sha256, entry.path);
    assert.equal(normalized.rawSha256, rawByPath.get(entry.path)?.deliveredRawSha256, `${entry.path}: delivered raw hash`);
  } else assert.equal(sha256(content), entry.sha256, entry.path);
}
process.stdout.write(`${JSON.stringify({ schema: 'backbond-blind-packet-verification-v1', fileCount: manifest.files.length, pass: true })}\n`);
