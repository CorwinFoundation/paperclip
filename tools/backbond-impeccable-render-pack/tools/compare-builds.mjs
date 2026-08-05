#!/usr/bin/env node
import assert from 'node:assert/strict'; import crypto from 'node:crypto'; import fs from 'node:fs'; import path from 'node:path';
const [a,b]=process.argv.slice(2); const ta=JSON.parse(fs.readFileSync(path.join(a,'candidate-tuple.json'))); const tb=JSON.parse(fs.readFileSync(path.join(b,'candidate-tuple.json')));
assert.deepEqual(ta,tb,'candidate tuples differ');
for (const name of ['image.oci.tar','oci-manifest.json','oci-config.json','pack.tar.gz','enabled-files.sha256','normalized-evidence.sha256','machine-audit.sha256','AUTHORITY-MANIFEST.json']) assert.deepEqual(fs.readFileSync(path.join(a,name)),fs.readFileSync(path.join(b,name)),name);
for (const name of fs.readdirSync(path.join(a,'blind-packet')).sort()) assert.deepEqual(fs.readFileSync(path.join(a,'blind-packet',name)),fs.readFileSync(path.join(b,'blind-packet',name)),`blind-packet/${name}`);
const authorityBytes=fs.readFileSync(path.join(a,'AUTHORITY-MANIFEST.json'));
assert.equal(ta.authority_manifest_digest,`sha256:${crypto.createHash('sha256').update(authorityBytes).digest('hex')}`,'authority manifest digest is not derived from delivered bytes');
process.stdout.write(`${JSON.stringify({schema:'backbond-reproducible-build-v1',tuple:ta,pass:true},null,2)}\n`);
