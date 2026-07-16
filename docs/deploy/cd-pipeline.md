# CD pipeline — Founder-gated, operator-mediated production delivery

The production scanner host has default-deny outbound network access. GitHub
Actions therefore builds and packages each release, while an operator sends the
approved files to the host over the existing inbound SSH channel. The host never
pulls an image or opens general egress.

The first migration from the systemd service is documented in
[`first-container-cutover.md`](first-container-cutover.md). This document
describes the release trust chain and the repeatable delivery flow.

## Release sequence

For every commit merged to `master`, `.github/workflows/cd.yml`:

1. Validates the deploy scripts.
2. Builds `ghcr.io/corwinfoundation/are-scanner:<git-sha>` from that exact event
   commit and sets the OCI revision label to the same SHA.
3. Pushes and keyless-signs the registry digest with Cosign.
4. Pulls that digest on the GitHub runner, saves the SHA-tagged image as a Docker
   archive, hashes the archive, and uploads `image-payload-<sha>`.
5. Opens an approval issue assigned to `CorwinFoundation`. The job does not
   continue until that account approves it.
6. Writes and signs a release manifest containing the Git SHA, image ref,
   registry digest, exact archive SHA-256, artifact name, approver, and Actions
   run URL. It uploads `release-manifest-<sha>`.
7. Records “approved — awaiting operator push” only after manifest signing and
   artifact upload succeed.

A manual workflow dispatch follows the same path and always builds the event's
current `master` SHA. It cannot relabel arbitrary or older source as a different
commit.

## Trust chain

The box trusts only the public key installed at
`/etc/are-scanner/cd-manifest-signing-pubkey.pem`. The public-key copy in the
Actions artifact is informational and is never accepted as a command-line trust
anchor.

`operator_push.sh` verifies the signed manifest before loading anything. It then
requires every operator-supplied field to equal the signed value. `box_deploy`
verifies the archive SHA-256, requires the expected tag to exist after load, and
requires the image's OCI revision label to equal the approved Git SHA.

This binds the following chain:

`approved Actions run → signed manifest → exact Docker archive → image tag and revision → activated container`

The registry digest remains in the signed audit record. Docker archives do not
reliably preserve registry `RepoDigest` metadata, so the on-box content check is
the signed archive SHA-256 rather than a post-load `RepoDigest` comparison.

## One-time GitHub setup

- Repository Issues must remain enabled for the approval gate.
- The Actions secret `CD_MANIFEST_SIGNING_KEY_PEM` must contain the private PEM
  key whose public half is installed on the box.
- `GITHUB_TOKEN` receives `packages: write` only in the image-build job and
  `issues: write` only where the approval gate needs it.
- The `production` Environment is retained as an audit label. The approval issue
  is the enforced Founder gate for this repository.
- `PAPERCLIP_API_URL` and `PAPERCLIP_DEPLOY_BOT_KEY` are optional for tracker
  posting; they do not weaken artifact verification.

## Per-release operator delivery

After the approval issue is approved, download both artifacts from the same
Actions run:

- `image-payload-<sha>` containing `are-scanner-<sha>.tar` and its checksum file.
- `release-manifest-<sha>` containing `release-manifest.json`, its signature, and
  an informational public-key copy.

Read `sha`, `ref`, `digest`, `tar_sha256`, `approver`, and `run_url` from the
manifest. Send only the archive, manifest, and signature to the box. Do not use
the downloaded public-key copy in place of the preinstalled trust anchor.

For an ordinary container-to-container release, run:

```sh
/root/deploy/operator_push.sh \
  --tarball /tmp/are-scanner-<sha>.tar \
  --tar-sha256 <tar_sha256> \
  --digest <digest> \
  --ref <ref> \
  --sha <sha> \
  --approver <approver> \
  --run-url <run_url> \
  --manifest /tmp/release-manifest.json \
  --manifest-sig /tmp/release-manifest.json.sig
```

Use `--first-cutover` only for the documented systemd-to-container migration.

## Failure and rollback behavior

- Signature, field, archive, ref, or revision mismatch: fail before activation.
- Candidate acceptance failure: remove the first-cutover candidate, or restart
  the previous retained image during later deployments.
- Nginx cutover or public health failure: restore the saved legacy Nginx config
  and verify the systemd-backed route.
- If ingress restoration is uncertain, keep the candidate running so Nginx is
  never intentionally pointed at a stopped process, and page the operator.
- Images are not pruned by the deployment scripts.

The `vapi` tenant remains prohibited in deployment acceptance. State and secrets
stay host-mounted under `/var/lib/are-scanner` and `/etc/are-scanner/secrets.env`;
they are never embedded in an artifact.
