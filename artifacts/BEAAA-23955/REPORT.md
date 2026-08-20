# QA BEAAA-23955 — canonical c4a58caa GHCR record

**Verdict: CHANGES REQUESTED**

- Candidate: `70e2632b9dfcce46e4d579328b4aec8fe18b89e2` (PR #203, `beaaa-23948-ghcr-canonical`)
- Base: `master@7f8be7a113d2418d4370117a430802303e43567b`
- Parent: BEAAA-23948
- Tested: 2026-08-17
- Scope: source review only. No merge, promote, activate, retag, or delete of either GHCR image.

## Candidate integrity

`70e2632b` has exactly one parent, `7f8be7a1`, which GitHub `list_commits` confirms is the
current tip of `master` (checked against the API, not a local remote-tracking ref). The diff
against the base is 5 files, 135 insertions, 1 deletion, `--diff-filter=D` empty:

```
M docs/deploy/scanner-deploy-runbook.md
A docs/deploy/scanner-image-c4a58caa-canonical.md
M docs/deploy/sequence-20-production-deployment-runbook.md
M scripts/deploy/scanner-deploy.sh
M tests/test_scanner_deploy_beaaa16598.py
```

`python3 -m pytest -q tests/test_scanner_deploy_beaaa16598.py` at the candidate: **39 passed**.

## Acceptance criterion 1 — canonical designation and rationale — PASS

The pushed digest is proven from the run's own log, not from the record's assertion.
`GET /repos/CorwinFoundation/are-scanner/actions/runs/32010676498/logs`, step
*Push immutable image and record repository digest*:

```
sha-c4a58caac801d24e7efa2436dbc5c4086b05d5fe: digest: sha256:b9bd0e70943424331c67c07ad17682412871d4cddde69d97193f847cd0fccfdd size: 1997
```

Run metadata: `head_sha = c4a58caac801d24e7efa2436dbc5c4086b05d5fe`, `conclusion = success`,
name `Scanner Immutable GHCR Image`. Every pipeline check the record credits the run with is
present in the logs and passed: exact-commit checkout (`git log -1 --format=%H` prints
`c4a58caa…`), SHA/target validation, OCI `source` and `revision` label assertions, embedded
release-identity assertions, and `runtime import closure ok`.

The embedded manifest, read from the artifact's own root filesystem, records
`scanner_version 0.5.0`, `corpus_version 0.2.0`, `build_sha c4a58caac801…`,
`release_tag sha-c4a58caac801…` — as the record states.

## Acceptance criterion 2 — helper fails before archive access — PASS (implementation)

The guard sits at lines 75–77 of `scripts/deploy/scanner-deploy.sh`, after argument syntax
validation and before the archive path check (78), the existence check (79), the checksum
(80), and `docker load` (90). I drove the real helper rather than reading the ordering off
the diff, with a **present, checksum-valid** archive at the fixed path so that no later
failure could be mistaken for the guard:

| case | arguments | result |
|---|---|---|
| A | `c4a58caa` + archive-imported digest, archive present and checksum valid | `ERROR: repository digest is not the canonical c4a58caa image` |
| B | `c4a58caa` + canonical digest | passes the guard, proceeds to `required path missing: /etc/backbond/scanner-edge.env` |
| C | different commit SHA + archive-imported digest | guard does not apply — `ERROR: archive not found` |
| D | `c4a58caa` + unrelated third-party digest | `ERROR: repository digest is not the canonical c4a58caa image` |

Case A is the criterion: the pairing is rejected while a valid archive is sitting on disk, so
the rejection is not archive-dependent. Case B confirms the guard is not blanket-blocking and
case C confirms it is scoped to the one commit. Case D confirms it fails closed on any
non-canonical digest, not just the known alternate.

## Acceptance criterion 3 — the three documents agree — PASS

All three name `sha256:b9bd0e70…` as canonical for `c4a58caac801…` and `sha256:f014212dca8f…`
as non-canonical, and all three state that the designation does not authorize promotion. The
pre-existing `0b565dda…` Sequence 20 pins are untouched. No contradiction between the files.

Two context notes, neither a defect in this candidate:

- `SCANNER_FROZEN_REPO_DIGEST` and `0e5889e3…` do not appear in `scripts/deploy/scanner-deploy.sh`
  at either the base or the candidate; the only in-tree occurrence of that variable is in the
  test file. The helper is argument-driven, which is what the new record says.
- The third tag convention the CTO raised (`<sha>-<run_id>`) is an uncommitted working-tree
  edit in the shared checkout. `master@7f8be7a1`'s workflow builds `sha-${{ github.sha }}`,
  matching the record. The candidate pins by digest, not by tag, so the drifting convention
  does not reach this change.

## Acceptance criterion 4 — filesystem comparison vs artifact evidence — PASS on the comparison, one correction needed elsewhere

### Evidence hashes

Downloaded artifact `9281655326`
(`scanner-immutable-image-c4a58caac801d24e7efa2436dbc5c4086b05d5fe`) directly:

| claimed in record | measured | verdict |
|---|---|---|
| ZIP SHA-256 `75696607d4fce58ab707919f0a48dc5c176d36047fe34fb0845d3042b9669d82` | identical | confirmed |
| Docker archive SHA-256 `0ee87a4be6f4948221a21dffc87500a1037f3e42e2cfa04a89a331fd21d93c2b` | identical | confirmed |
| deployment-host archive SHA-256 `a5460c36cc50a47da0f80acf58a58821364abd1eea06ba0510d64b6162bea2e8` | not reproducible from this seat | see below |

The third value is carried forward from BEAAA-23220. That archive is not on this host
(`/tmp/beaaa-22801-stage/c4a58caac801…/` is empty) and `a5460c36…` has no in-repo occurrence
before this commit. I did not verify it and the record should not be read as if a QA seat had.
It is also not load-bearing for the comparison, because I reproduced the comparison from the
live image rather than from that archive.

### Independent reproduction of the comparison

I rebuilt both root filesystems from layer tars — the CI side from the downloaded artifact,
the alternate side from `docker save` of the local
`ghcr.io/corwinfoundation/are-scanner:c4a58caac801…` image, whose `RepoDigests` is
`…@sha256:f014212dca8f…`. Nothing was loaded into the daemon and neither GHCR image was
touched. Script: `qa_23955_rootfs_compare.py`; full output: `fs-compare-independent.txt`.

Layer-derived totals: 14,840 entries each side, 1,730 of them directories, so 13,110
non-directory entries. The record counts an **exported** root filesystem, so I measured
Docker's container-injected set separately using an unrelated local image
(`python:3.11.15-slim-bookworm`) — `docker export` adds `.dockerenv`, `dev/console`,
`etc/hosts`, `etc/mtab` (four non-directory) plus `dev/pts`, `dev/shm` (two directories), and
none of those six exist in the scanner layers. That reconciles the record's numbers exactly:

| claim in record | independently measured | verdict |
|---|---|---|
| same 13,114 entries, no path additions or removals | 13,110 + 4 injected = **13,114**; only-in-CI 0, only-in-alt 0 | confirmed |
| 10,666 byte-identical | 10,662 + 4 injected (all empty or container-independent) = **10,666** | confirmed |
| 2,446 `.pyc` files differing only in the 16-byte header | **2,446**; SHA-256 of every byte after offset 16 matches in all cases | confirmed |
| `/etc/group-` differs only in the password-field marker | only difference is `*` (CI) vs `x` (alt) across all lines | confirmed |
| `/etc/shadow` differs only in the `scanner` last-change day | `scanner:!:20682::::::` vs `scanner:!:20679::::::` | confirmed |
| no `/app` file differed | 0 differing paths under `app/` | confirmed |
| embedded release manifest and `.backbond-source-commit` byte-identical | both identical | confirmed |

Total differing entries: 2,448 = 2,446 `.pyc` + the two `/etc` files. Nothing else differs.
The record's filesystem comparison is accurate.

## Findings

### F1 — P2 — the record states that all eight layer digests differ; four are identical

`docs/deploy/scanner-image-c4a58caa-canonical.md`, "Image comparison":

> The two images are not byte-identical. Their Docker configs and all eight layer
> digests differ.

The configs do differ (`aedb5b64…` vs `1e6366d0…`). The layer claim is false. The two images
share their first four layers exactly — the `python:3.11-slim-bookworm` base:

```
CI  diff_ids: 6f943283 275f0a9e 3d7dddf6 1a8800d1 | 0574a679 00f9ef10 3b9329c2 6bc716f1
alt diff_ids: 6f943283 275f0a9e 3d7dddf6 1a8800d1 | 0ff816bf 3641ad62 47a58099 0b38133a
```

Repro: `docker image inspect ghcr.io/corwinfoundation/are-scanner:c4a58caac801… --format '{{json .RootFS.Layers}}'`
against `rootfs.diff_ids` in the artifact's config blob
`blobs/sha256/aedb5b646394faa1afee049317233c894d00b7b718159ab4090413b66cf455ca`.

This also holds for the compressed forms, so it is not an artefact of reading diff IDs rather
than registry descriptors. The push step logs `6f9432833129: Layer already exists`, and the
compressed base blobs from `docker save` of the alternate image (`26c307b5…`, `7ce8f1a1…`,
`daff6f8a…`, `605d2cb1…`) all appear in run 32010676498's own logs.

Expected: a statement matching the evidence. Actual: a statement that overstates the
divergence by four layers, in the document the two runbooks now cite as the authoritative
provenance record.

Bounded correction — replace the sentence with something like: *their Docker configs and the
final four layer digests differ; the four base-image layers are identical.* No other part of
the record depends on it, and the canonical designation is unaffected.

### F2 — P3 — the new helper test passes even when the guard stops failing closed

`tests/test_scanner_deploy_beaaa16598.py::test_c4a58caa_wrong_repository_digest_fails_before_archive_access`
asserts only `returncode != 0` and that the guard's message appears in stderr. Its fixture
points at an archive that does not exist, so a guard that merely warns still produces a
non-zero exit from the later `archive not found`, with the guard's message already on stderr.

Mutation run against the candidate (whole module, 39 tests):

| mutant | result |
|---|---|
| M1 delete the guard block | killed — 2 failed |
| M2 canonical constant → the archive-imported digest | killed — 2 failed |
| M3 invert the condition (`!=` → `=`) | killed — 1 failed |
| M4 move the guard after the archive checksum check | killed — 1 failed |
| M5 wrong `CANONICAL_C4_SHA` constant | killed — 2 failed |
| **M6 `die` → `log` (warn and continue)** | **SURVIVED — 39 passed** |

The shipped implementation is correct; I confirmed fail-closed behaviour directly in case A
above. The gap is in the test, which cannot distinguish "rejected at the guard" from "warned,
then failed later for an unrelated reason" — so it would not catch a future regression to
warn-and-continue.

Bounded correction — write a real, checksum-valid archive at the fixed candidate path in the
fixture (as case A does) so the guard is the only possible cause of failure, or assert that
`archive not found` is absent from stderr.

## What passes

The canonical designation itself is sound and fully evidenced, the deploy guard behaves
correctly and fails closed, the three documents agree, and the filesystem comparison
reproduces exactly. Both findings are bounded edits to one sentence and one test fixture.

## Evidence in this directory

- `fs-compare-independent.txt` — full output of the independent root-filesystem comparison
- `qa_23955_rootfs_compare.py` — the comparison script
- `artifact-scanner-edge-image.digest` — digest file from artifact 9281655326
- `run-32010676498-push-step.txt` — push step log showing the pushed digest
