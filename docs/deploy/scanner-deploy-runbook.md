# Scanner deploy runbook — systemd-owned live edge

**Ticket:** BEAAA-16598 (parent BEAAA-16355; live-verified against
`462aee92358993eb21f29d74d6f690765db3ae06` on `srv1749248`).
**Contract:** `are-scanner-api-v1@v1.1.0` (unchanged). No score / rubric / corpus /
API response contract change; `scanner_version 0.5.0`, `corpus_version 0.1.0`.

This is the **single active deploy runbook** for putting a merged `master` commit
live on the edge host. Superseded CD-pipeline docs are preserved under
[`docs/deploy/history/`](history/) as evidence; retired ADR-0057 on-box deploy
verb and push-orchestrator scripts (BEAAA-13362) are preserved under
[`scripts/deploy/history/`](../../scripts/deploy/history/).

> Scope note: `scan-log-backup-runbook.md` (scan-log backup ops) and
> `sequence-20-stage-gate-runbook.md` (Seq 20 stage-gate governance) also live in
> this directory. They document distinct operational/governance concerns, not the
> deploy mechanism below, and are intentionally retained.

## Verified topology (srv1749248)

| element | value |
|---|---|
| systemd unit | `backbond-scanner-live.service` (owns the container lifecycle) |
| env file | `/etc/backbond/scanner-edge.env` (`0600`) |
| live image variable | `SCANNER_EDGE_IMAGE` in that env file |
| local live tag | `backbond-scanner-live:<git-sha>` |
| container name | `backbond-scanner-live-edge` |
| app readiness | `http://127.0.0.1:8077/openapi.json` |
| image selector | OCI label `org.opencontainers.image.revision=<git-sha>` |

The host runs **default-deny egress**: it can NOT `docker pull`. The image is
delivered as a tarball over the operator's inbound SSH channel and loaded
locally. The GHCR ref produced at build time is signed provenance only and is
**never** the live tag.

## Two scripts, one path

1. `scripts/deploy/dev_deploy.sh` — runs on the agent/CI side after a merge to
   `master`. It builds `--build-arg GIT_SHA=<sha>` (which bakes the revision
   label), pushes and `cosign`-signs the GHCR provenance ref, `docker save`s the
   tar, records `archive.sha256`, and then **emits — but never runs** a single
   operator command. The agent side has no SSH key and opens no SSH connection.

2. `scripts/deploy/scanner-deploy.sh <git-sha> [tar]` — runs **on the box** as
   `sudo /usr/local/sbin/scanner-deploy`. It:
   1. `docker load`s the SCP-delivered tar (no pull),
   2. selects the image by exact `org.opencontainers.image.revision=<sha>` and
      re-inspects the winner,
   3. retags it to `backbond-scanner-live:<sha>`,
   4. backs up `/etc/backbond/scanner-edge.env` to `.pre-<sha>` **before** any
      mutation,
   5. repoints `SCANNER_EDGE_IMAGE` at the local live tag (all other lines
      preserved),
   6. `systemctl restart backbond-scanner-live.service`,
   7. waits boundedly for `http://127.0.0.1:8077/openapi.json`,
   8. on **any** post-backup failure, restores `.pre-<sha>` and restarts the
      retained prior image, then exits non-zero.

## Operator procedure

```bash
# On the agent/CI side, on green master:
scripts/deploy/dev_deploy.sh
# Copy the single emitted command and run it as the operator:
scp <stage>/backbond-scanner-<sha>.tar are-deploy@srv1749248:/var/tmp/backbond-scanner-<sha>.tar \
  && ssh are-deploy@srv1749248 'set -euo pipefail; cleanup() { rm -f /var/tmp/backbond-scanner-<sha>.tar; }; trap cleanup EXIT; sudo /usr/local/sbin/scanner-deploy <sha> /var/tmp/backbond-scanner-<sha>.tar'
```

A non-zero exit means the helper already rolled the box back to the prior image.

## Install / update the on-box helper

```bash
sudo install -m 0755 -o root -g root scripts/deploy/scanner-deploy.sh /usr/local/sbin/scanner-deploy
# sudoers whitelist:
#   are-deploy ALL=(root) NOPASSWD: /usr/local/sbin/scanner-deploy
```

## Rollback

- **Automatic:** any failure after the env backup restores
  `/etc/backbond/scanner-edge.env.pre-<sha>` and restarts the retained prior
  image.
- **Manual (later regression):** repoint `SCANNER_EDGE_IMAGE` to the prior
  `backbond-scanner-live:<prev-sha>` (still loaded locally) and
  `sudo systemctl restart backbond-scanner-live.service`, or restore a
  `.pre-<sha>` backup.
- **Candidate:** revert the candidate commit.

## Promotion gate

No score/rubric/corpus/API change; calibration delta is Δ0 by construction
(this change touches only deploy machinery and docs). A fresh QA Engineer
exact-SHA `APPROVED` is required before merge; any live/shared promotion
additionally requires Founder sign-off (BEAAA-15373 / BEAAA-15399 / BEAAA-15400).
