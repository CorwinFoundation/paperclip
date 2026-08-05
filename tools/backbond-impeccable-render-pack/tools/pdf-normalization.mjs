import crypto from 'node:crypto';

export const PDF_NORMALIZATION_SCHEMA = 'backbond-pdf-normalization-v2';
export const PDF_NORMALIZATION_TOOL = Object.freeze({ runtime: 'node-buffer-latin1', version: process.versions.node, fields: ['/CreationDate', '/ModDate'], replacement: "D:19700101000000+00'00'", canonicalTaggedNodeIds: true });
const sha256 = (buffer) => crypto.createHash('sha256').update(buffer).digest('hex');

export function normalizePdfMetadata(buffer) {
  const rawSha256 = sha256(buffer);
  let text = buffer.toString('latin1');
  for (const field of PDF_NORMALIZATION_TOOL.fields) {
    const pattern = new RegExp(`\\${field} \\(D:\\d{14}\\+00'00'\\)`, 'g');
    const matches = text.match(pattern) || [];
    if (matches.length !== 1) throw new Error(`${field}: expected exactly one proven volatile field`);
    text = text.replace(pattern, `${field} (${PDF_NORMALIZATION_TOOL.replacement})`);
  }
  const nodeIds = new Map();
  text = text.replace(/node\d{8}/g, (id) => {
    if (!nodeIds.has(id)) nodeIds.set(id, `node${String(nodeIds.size + 1).padStart(8, '0')}`);
    return nodeIds.get(id);
  });
  const normalized = Buffer.from(text, 'latin1');
  if (normalized.length !== buffer.length) throw new Error('PDF normalization changed byte length');
  return { rawSha256, normalizedSha256: sha256(normalized), normalized, schema: PDF_NORMALIZATION_SCHEMA, tool: PDF_NORMALIZATION_TOOL };
}
