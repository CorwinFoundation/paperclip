#!/bin/sh
set -eu
label=${1:?usage: build-candidate.sh OUTPUT_DIR}
case "$label" in */*|.*|'') echo 'output must be a new child directory' >&2; exit 64;; esac
test ! -e "$label"
test -z "$(git status --porcelain)"
export SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)"
node tools/generate-manifests.mjs
test -z "$(git status --porcelain)"
mkdir "$label"
mkdir "$label/oci-layout"
docker buildx build \
  --platform linux/amd64 \
  --network=none \
  --provenance=false \
  --sbom=false \
  --tag "backbond-phase2:candidate" \
  --output "type=oci,dest=$label/oci-layout,tar=false,rewrite-timestamp=true" \
  . >"$label/docker-build.log"
node --input-type=module - "$label" <<'NODE'
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const out = process.argv[2];
const layout = path.join(out, 'oci-layout');
const index = JSON.parse(fs.readFileSync(path.join(layout, 'index.json')));
if (index.manifests.length !== 1) throw new Error('OCI index must contain exactly one manifest');
const manifestDescriptor = index.manifests[0];
const blob = digest => path.join(layout, 'blobs', 'sha256', digest.replace(/^sha256:/, ''));
const manifestBytes = fs.readFileSync(blob(manifestDescriptor.digest));
const manifestDigest = `sha256:${crypto.createHash('sha256').update(manifestBytes).digest('hex')}`;
if (manifestDigest !== manifestDescriptor.digest) throw new Error('OCI manifest descriptor digest mismatch');
const manifest = JSON.parse(manifestBytes);
const configBytes = fs.readFileSync(blob(manifest.config.digest));
const configDigest = `sha256:${crypto.createHash('sha256').update(configBytes).digest('hex')}`;
if (configDigest !== manifest.config.digest) throw new Error('OCI config descriptor digest mismatch');
fs.writeFileSync(path.join(out, 'oci-manifest.json'), manifestBytes);
fs.writeFileSync(path.join(out, 'oci-config.json'), configBytes);
fs.writeFileSync(path.join(out, 'image-digest.txt'), `${manifestDigest}\n`);
fs.writeFileSync(path.join(out, 'image-config-digest.txt'), `${configDigest}\n`);
NODE
tar --sort=name --mtime="@$SOURCE_DATE_EPOCH" --owner=0 --group=0 --numeric-owner \
  -C "$label/oci-layout" -cf "$label/image.oci.tar" .
load_output=$(docker load -i "$label/image.oci.tar")
loaded_tag=$(printf '%s\n' "$load_output" | sed -n 's/^Loaded image: //p' | tail -n 1)
test -n "$loaded_tag"
image=$(docker image inspect "$loaded_tag" --format '{{index .RepoDigests 0}}')
test "${image#*@}" = "$(cat "$label/image-digest.txt")"
printf '%s\n' "$image" >"$label/image-id.txt"
BACKBOND_RENDER_IMAGE="$image" node tools/image-audit.mjs "$image" >"$label/image-audit.json"
BACKBOND_RENDER_IMAGE="$image" node tools/security-acceptance.mjs >"$label/security-acceptance.json"
BACKBOND_RENDER_IMAGE="$image" BACKBOND_BENCHMARK_OUTPUT="$PWD/$label/evidence" node tools/run-benchmark.mjs
node tools/audit-accessibility.mjs "$label/evidence"
node tools/build-blind-packet.mjs "$label/evidence" "$label/blind-packet"
node tools/verify-blind-packet.mjs "$label/blind-packet" >"$label/blind-packet-verification.json"
node tools/n5-no-score-delta.mjs >"$label/n5-no-score-delta.json"
find bin lib tools benchmark -type f -print0 | sort -z | xargs -0 sha256sum >"$label/enabled-files.sha256"
find "$label/evidence" -type f ! -name '*.pdf' -print0 | sort -z | xargs -0 sha256sum | sed "s#$label/##" >"$label/normalized-evidence.sha256"
find "$label/evidence" -name audit.json -type f -print0 | sort -z | xargs -0 sha256sum | sed "s#$label/##" >"$label/machine-audit.sha256"
git archive --format=tar --prefix=backbond-impeccable-render-pack/ HEAD | gzip -n -9 >"$label/pack.tar.gz"
docker image inspect "$image" --format '{{json .RootFS.Layers}}' >"$label/layers.json"
node --input-type=module - "$label" "$image" <<'NODE'
import crypto from 'node:crypto'; import fs from 'node:fs'; import path from 'node:path';
const [out]=process.argv.slice(2); const h=(b)=>`sha256:${crypto.createHash('sha256').update(b).digest('hex')}`; const read=(p)=>fs.readFileSync(path.join(out,p));
const manifestDigest=fs.readFileSync(path.join(out,'image-digest.txt'),'utf8').trim();
const configDigest=fs.readFileSync(path.join(out,'image-config-digest.txt'),'utf8').trim();
if (!/^sha256:[0-9a-f]{64}$/.test(manifestDigest) || !/^sha256:[0-9a-f]{64}$/.test(configDigest)) throw new Error('invalid OCI descriptor digest');
const authorityBytes=fs.readFileSync('AUTHORITY-MANIFEST.json');
const authority=JSON.parse(authorityBytes);
if (authority.schema !== 'backbond-phase-two-authority-manifest-v1') throw new Error('invalid authority manifest schema');
if (authority.rubricVersion !== 'phase2-convergence-1.0.0') throw new Error('authority manifest rubric mismatch');
if (authority.promotionAuthorized !== false) throw new Error('authority manifest must prohibit promotion');
fs.writeFileSync(path.join(out,'AUTHORITY-MANIFEST.json'),authorityBytes);
fs.writeFileSync(path.join(out,'candidate-tuple.json'),`${JSON.stringify({git_sha:process.env.GIT_SHA||'',source_date_epoch:process.env.SOURCE_DATE_EPOCH,lockfile_digest:h(fs.readFileSync('package-lock.json')),oci_manifest_digest:manifestDigest,oci_config_digest:configDigest,portable_oci_archive_digest:h(read('image.oci.tar')),pack_archive_digest:h(read('pack.tar.gz')),pack_version:JSON.parse(fs.readFileSync('package.json')).version,enabled_file_manifest_digest:h(read('enabled-files.sha256')),normalized_evidence_manifest_digest:h(read('normalized-evidence.sha256')),blind_packet_digest:h(read('blind-packet/packet-manifest.json')),machine_audit_summary_digest:h(read('machine-audit.sha256')),authority_manifest_digest:h(authorityBytes),rubric_manifest_digest:h(fs.readFileSync('SCORING-RUBRIC.md')),rubric_version:authority.rubricVersion},null,2)}\n`);
NODE
GIT_SHA=$(git rev-parse HEAD) node --input-type=module - "$label/candidate-tuple.json" <<'NODE'
import fs from 'node:fs'; const p=process.argv[2]; const x=JSON.parse(fs.readFileSync(p)); x.git_sha=process.env.GIT_SHA; fs.writeFileSync(p,`${JSON.stringify(x,null,2)}\n`);
NODE
