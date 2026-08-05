const DIGEST = /^sha256:[0-9a-f]{64}$/;

export function canonicalImageIdentity(image, repoDigests) {
  if (!Array.isArray(repoDigests)) throw new Error('docker image inspect returned invalid RepoDigests');
  const digests = [...new Set(repoDigests.map((reference) => reference.split('@').at(-1)).filter((digest) => DIGEST.test(digest)))];
  if (digests.length !== 1) throw new Error(`BACKBOND_RENDER_IMAGE must resolve to exactly one OCI manifest digest; found ${digests.length}`);
  const requestedDigest = image.includes('@') ? image.split('@').at(-1) : image;
  if (DIGEST.test(requestedDigest) && image.includes('@') && requestedDigest !== digests[0]) throw new Error('BACKBOND_RENDER_IMAGE digest does not match inspected OCI manifest digest');
  return digests[0];
}
