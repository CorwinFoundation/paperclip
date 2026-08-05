#!/usr/bin/env node
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { sha256 } from '../lib/policy.mjs';
import { normalizePdfMetadata } from './pdf-normalization.mjs';

const evidence = path.resolve(process.argv[2] || 'evidence');
const output = path.resolve(process.argv[3] || 'blind-packet');
if (fs.existsSync(output)) throw new Error('blind packet destination exists');
fs.mkdirSync(output, { recursive: true });
const seed = process.env.BACKBOND_BLIND_SEED || 'beaaa-20833-phase2-convergence-1.0.0';
const files = [];
const rawPdfHashes = [];
for (const surface of ['marketing', 'report', 'scanner']) {
  const baselineAudit = fs.readFileSync(path.join(evidence, surface, 'baseline', 'audit.json'));
  const revisionAudit = fs.readFileSync(path.join(evidence, surface, 'revision', 'audit.json'));
  const baselinePng = fs.readFileSync(path.join(evidence, surface, 'baseline', 'desktop.png'));
  const revisionPng = fs.readFileSync(path.join(evidence, surface, 'revision', 'desktop.png'));
  assert.notEqual(sha256(baselineAudit), sha256(revisionAudit), `${surface}: semantic/audit diff absent`);
  assert.notEqual(sha256(baselinePng), sha256(revisionPng), `${surface}: pixel diff absent`);
  const flip = crypto.createHash('sha256').update(`${seed}:${surface}`).digest()[0] & 1;
  const mapping = flip ? { A: 'revision', B: 'baseline' } : { A: 'baseline', B: 'revision' };
  for (const label of ['A', 'B']) for (const name of ['desktop.png', 'tablet.png', 'mobile-390.png', 'print.pdf', 'audit.json']) {
    const from = path.join(evidence, surface, mapping[label], name);
    const to = path.join(output, `${surface}-${label}-${name}`);
    fs.copyFileSync(from, to);
    const content = fs.readFileSync(to);
    if (name.endsWith('.pdf')) {
      const normalized = normalizePdfMetadata(content);
      fs.writeFileSync(to, normalized.normalized);
      files.push({ path: path.basename(to), sha256: normalized.normalizedSha256, normalizationSchema: normalized.schema });
      rawPdfHashes.push({ path: path.basename(to), deliveredRawSha256: normalized.normalizedSha256, normalizedSha256: normalized.normalizedSha256, schema: normalized.schema, tool: normalized.tool });
    } else files.push({ path: path.basename(to), sha256: sha256(content) });
  }
  fs.writeFileSync(path.join(output, `${surface}-sealed-mapping.json`), `${JSON.stringify({ surface, seedSha256: sha256(Buffer.from(seed)), mapping })}\n`, { mode: 0o600 });
}
fs.writeFileSync(path.join(output, 'packet-manifest.json'), `${JSON.stringify({ schema: 'backbond-blind-packet-v1', rubricVersion: 'phase2-convergence-1.0.0', files: files.sort((a,b) => a.path.localeCompare(b.path)) }, null, 2)}\n`);
fs.writeFileSync(path.join(output, 'pdf-raw-hashes.json'), `${JSON.stringify({ schema: 'backbond-delivered-pdf-hashes-v2', tupleIdentity: true, files: rawPdfHashes.sort((a,b) => a.path.localeCompare(b.path)) }, null, 2)}\n`);
