#!/usr/bin/env node
import { preview } from '../lib/preview.mjs';
import { render } from '../lib/render.mjs';
import fs from 'node:fs';
import path from 'node:path';

const [mode, ...args] = process.argv.slice(2);
const get = (name) => {
  const at = args.indexOf(name);
  if (at < 0 || !args[at + 1]) throw new Error(`missing ${name}`);
  return args[at + 1];
};

try {
  const output = get('--output');
  if (mode === 'render') await render({ input: get('--input'), output });
  else if (mode === 'preview') await preview({ input: get('--input'), output, plan: get('--plan') });
  else throw new Error('mode must be render or preview');
  const files = {};
  for (const name of fs.readdirSync(output).sort()) {
    const absolute = path.join(output, name);
    if (!fs.statSync(absolute).isFile()) throw new Error('nested output denied');
    files[name] = fs.readFileSync(absolute).toString('base64');
  }
  process.stdout.write(`${JSON.stringify({ schema: 'backbond-bounded-output-v1', files })}\n`);
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 64;
}
