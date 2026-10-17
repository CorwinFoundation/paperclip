# Scanner deploy runbook — systemd-owned live edge

**Tickets:** BEAAA-16598 established the path; BEAAA-22831 made the helper
Development Mode-capable.
**Contract:** `are-scanner-api-v1@v1.1.0` (unchanged). No score / rubric / corpus /
API response contract change; `scanner_version 0.5.0`, `corpus_version 0.2.0`.

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
| targeted-attempt ledger | `/var/lib/are-scanner/full-scan-target-attempts.sqlite3` (service-owned persistent volume; fixed by the unit) |
| live image variable | `SCANNER_EDGE_IMAGE` in that env file |
| local live tag | `backbond-scanner-live:<git-sha>` |
| container name | `backbond-scanner-live-edge` |
| app readiness | `http://127.0.0.1:8077/openapi.json` |
| image selector | OCI label `org.opencontainers.image.revision=<git-sha>` |

The checked-in unit sets `ARE_FULL_SCAN_ATTEMPT_LEDGER_PATH` to the absolute
service-owned path above. Keep it on the `/var/lib/are-scanner` volume: moving
the evidence root or restarting/replacing the container must reopen the same
target-send reservations. A missing or relative override is a startup/config
error; never redirect it to a container-temporary path.

The host runs **default-deny egress**: it can NOT `docker pull`. The image is
delivered as a tarball over the operator's inbound SSH channel and loaded
locally. The GHCR ref produced at build time is signed provenance only and is
**never** the live tag.

## Two scripts, one path

1. `scripts/deploy/dev_deploy.sh` — runs on the agent/CI side after a merge to
   `master`. It builds `--build-arg GIT_SHA=<sha>` (which bakes the revision
   label), pushes and `cosign`-signs the GHCR provenance ref, `docker save`s the
   tar, records `archive.sha256`, and then **emits — but never runs** a single
   operator command.

2. `scripts/deploy/scanner-deploy.sh <git-sha> <tar> <repository@sha256:digest> <archive-sha256> <candidate-id>` — runs **on the box** as
   `sudo /usr/local/sbin/scanner-deploy`. It:
   1. `docker load`s the SCP-delivered tar (no pull),
   2. selects the image by exact `org.opencontainers.image.revision=<sha>` and
      re-inspects the winner,
   3. retags it to `backbond-scanner-live:<sha>`,
   4. snapshots the environment, current-version marker, and release identities
      in a retained transaction directory **before** any mutation,
   5. repoints `SCANNER_EDGE_IMAGE` at the local live tag (all other lines
      preserved),
   6. `systemctl restart backbond-scanner-live.service`,
   7. waits boundedly for `http://127.0.0.1:8077/openapi.json`,
   8. on **any** post-backup failure, restores `.pre-<sha>` and restarts the
      retained prior image, then exits non-zero.

## Operator procedure

```bash
# On the agent/CI side, on green master:
BACKBOND_CANDIDATE_ID=BEAAA-22801 scripts/deploy/dev_deploy.sh
# Copy the single emitted command and run it as the operator:
scp <stage>/backbond-scanner-<sha>.tar are-deploy@srv1749248:/var/tmp/backbond-scanner-<sha>.tar \
  && ssh are-deploy@srv1749248 'set -euo pipefail; cleanup() { rm -f /var/tmp/backbond-scanner-<sha>.tar; }; trap cleanup EXIT; sudo /usr/local/sbin/scanner-deploy <sha> /var/tmp/backbond-scanner-<sha>.tar <repository@sha256:digest> <archive-sha256> <candidate-id>'
```

A preflight failure leaves the live image, scan admissions, and activation state
untouched. A failure after activation starts invokes rollback; check its recorded
outcome rather than assuming rollback verification succeeded.

## Canonical c4a58caa repository reference

Commit `c4a58caac801d24e7efa2436dbc5c4086b05d5fe` has two immutable images in
GHCR. The canonical repository reference is the image built and checked by
GitHub Actions run `32010676498`:

```text
ghcr.io/corwinfoundation/are-scanner@sha256:b9bd0e70943424331c67c07ad17682412871d4cddde69d97193f847cd0fccfdd
```

Do not supply the archive-imported digest `sha256:f014212dca8f…` for this
commit. The helper rejects that pairing before archive access. The comparison
and provenance record is `scanner-image-c4a58caa-canonical.md`. This reference
does not authorize promotion.

## Install / update the on-box helper

Provision DNS trust anchors before installing or updating the helper. The host
blocks outbound HTTPS, so fetch IANA's current XML on the operator machine over
verified HTTPS and transfer it over SSH. The installer selects only currently
valid root DS records, replaces its managed file atomically, restarts the resolver,
and verifies root DNSKEY resolution. If verification fails, it restores the prior
managed file and restarts the resolver again. DNSSEC settings remain unchanged.

First check existing `*.positive` files. The installer rejects another root-anchor
source before making changes: duplicate records can crash systemd-resolved 255.
On the host repaired October 13, archive the recovery file under
`/root/backbond-dns-provision/`, then rename
`/etc/dnssec-trust-anchors.d/iana-root-2026.positive` to
`/etc/dnssec-trust-anchors.d/iana-root.positive` before running the installer.
Rename only when the destination does not already exist. The active resolver
retains its in-memory anchors during this migration; restart and verify it after
the installation completes.

```bash
# Operator machine, from the repository root. Fetch fresh data on every provision
# or refresh; do not reuse a historical anchor snapshot for a replacement host.
python3 scripts/deploy/provision_host_dns.py fetch --output root-anchors.xml
ssh root@srv1749248 'install -d -m 0700 /root/backbond-dns-provision'
scp root-anchors.xml scripts/deploy/provision_host_dns.py root@srv1749248:/root/backbond-dns-provision/
ssh root@srv1749248 'python3 /root/backbond-dns-provision/provision_host_dns.py install --source /root/backbond-dns-provision/root-anchors.xml'
```

The managed host file is `/etc/dnssec-trust-anchors.d/iana-root.positive` (0644).
Keep one managed root-anchor source so
future refreshes do not leave obsolete keys in a second local file.

Repeat the fetch/install procedure during host maintenance and before IANA root
key rollovers. `systemd-resolved` 255 does not automatically acquire new trust
anchors. Its current-key data must be maintained; a static recovery key is not an
automatic update mechanism. IANA publishes rollover dates at
<https://www.iana.org/dnssec/files>.

```bash
sudo install -m 0755 -o root -g root scripts/deploy/scanner-deploy.sh /usr/local/sbin/scanner-deploy
# sudoers whitelist:
#   are-deploy ALL=(root) NOPASSWD: /usr/local/sbin/scanner-deploy
sudo /usr/local/sbin/scanner-deploy preflight
```

Every activation also runs this preflight before image cleanup/loading, acquiring
the scan-admission lock, or creating a transaction. It makes a direct request to
the public SLA endpoint through the host resolver with a 5-second connect timeout
and 15-second total limit. A DNS failure names the resolver/trust-anchor check;
HTTP, TLS, and timeout failures report the curl exit code. It does not repair DNS
automatically, bypass TLS verification, or weaken DNSSEC.

## Rollback

- **Automatic:** any failure after the transaction snapshot restores the prior
  environment, markers, and identities, then restarts and verifies the retained
  prior image.
- **Manual (later regression):** repoint `SCANNER_EDGE_IMAGE` to the prior
  `backbond-scanner-live:<prev-sha>` (still loaded locally) and
  `sudo systemctl restart backbond-scanner-live.service`, or restore a
  `.pre-<sha>` backup.
- **Candidate:** revert the candidate commit.

## Development Mode gate

The helper validates the five generated identity inputs and does not contain a
historical candidate pin. The calling promotion issue must still establish the
current master identity, applicable QA evidence, authorization, and rollback
point before executing the emitted command.
