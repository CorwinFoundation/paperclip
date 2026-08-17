# BEAAA-21513 — QA report: read-only Scanner image verification

**Verdict: CHANGES REQUESTED**

- Candidate under test: `5d52651a473cada3c639f38c5bec1d6fc4933044` (branch `agent/beaaa-21512-readonly-verify`)
- Parent: BEAAA-21512 · Directive ancestor: BEAAA-21509
- Default branch at test time: `master` = `ae14923083d3b002de3926b8eff6fabb26038c89`
- Tested: 2026-08-17, in a detached worktree at the exact candidate SHA; the shared workspace was not modified.
- Files in candidate: `.github/workflows/scanner-image-readonly-verify.yml`, `scripts/verify_scanner_image.py`, `tests/test_scanner_image_readonly_verify_beaaa21512.py` (313 insertions, no other files touched).

## What passed

| Check | Result |
|---|---|
| `pytest -q tests/test_scanner_image_readonly_verify_beaaa21512.py` | 6 passed in 0.27s |
| Top-level `permissions` parsed | exactly `{contents: read, packages: read}` |
| Job-level `permissions` override | none present |
| SSH / cosign / `secrets.` / `id-token` / write scopes / deploy / production | none present |
| Pinned source commit `7850289333ffc1de6eebea9c2201e0f1059c4c12` | exists, authored 2026-07-31, **ancestor of `master`** → `git fetch --depth=1 origin <SHA>` will resolve |
| `EXPECTED_MANIFEST_SHA256` `2417a7b2…57ddc` | **independently recomputed** from `7850289…:release/backbond-scanner-release.json` → exact match |
| GHCR tag shape `…:sha-<revision>` | matches the publisher `sign-beaaa-19984.yml` (`${IMAGE_REPOSITORY}:sha-${GITHUB_SHA}`) |
| Artifact member names / checksum format | match producer `scanner-edge-image.yml` (`scanner-edge-image.tar`, `sha256sum` output shape) |
| `actions/checkout@v6`, `actions/upload-artifact@v7`, `docker/login-action@v3` | all exist upstream |
| Fail-closed behaviour of `verify_scanner_image.py` | **9/9** — see `fail-closed-matrix.out` |

The script's runtime behaviour is correct. Driving the real CLI entrypoint with the workflow's exact argument shape, every mismatch path exits 1, prints a specific reason, and leaves the pre-seeded `status: FAIL` evidence document untouched. That includes a self-consistent forged artifact (tampered tar plus a matching checksum bundled in the same zip), which is rejected by the external zip-hash pin.

## Defects

### D1 — BLOCKER: the workflow cannot succeed; `actions: read` is missing

The step *Download named Actions artifact using read-only API* (lines 69–88) calls, with `${{ github.token }}`:

```
GET ${GITHUB_API_URL}/repos/${GITHUB_REPOSITORY}/actions/artifacts/${SOURCE_ARTIFACT_ID}
GET ${GITHUB_API_URL}/repos/${GITHUB_REPOSITORY}/actions/artifacts/${SOURCE_ARTIFACT_ID}/zip
```

Both endpoints are on the Actions API surface and require the `actions` scope at read level. Declaring a `permissions:` block sets every scope not listed to `none`, so this workflow grants `actions: none`.

Supporting evidence gathered here:
- `CorwinFoundation/are-scanner` is **private**: unauthenticated `GET /repos/CorwinFoundation/are-scanner` and `GET …/actions/artifacts/8809120470` both return HTTP 404. There is no anonymous read path to fall back on.
- No workflow anywhere in the repo declares an `actions:` scope, and this candidate is the repo's only consumer of the Actions artifacts REST API — so no in-repo precedent shows this working.

`curl --fail-with-body` exits non-zero on HTTP ≥ 400 and the step runs under `set -euo pipefail`, so the expected 403 fails the step, fails the job, and the lane can never reach `status: PASS`.

This also makes the acceptance criterion self-contradictory as written: *"permissions are exactly `contents: read` and `packages: read`"* and *"the workflow proves the named Actions artifact"* cannot both hold. **This needs a CTO decision, not just a code fix** — either add `actions: read` and amend the acceptance scope to `contents: read`, `packages: read`, `actions: read`, or drop the Actions-artifact binding from the lane.

Not yet observed in a live run: the workflow has never executed (see *Live dispatch* below). The prediction rests on GitHub's documented permission model plus the private-repo and no-precedent evidence above.

### D2 — MAJOR: half the acceptance-scope suite's mutants survive

Mutation-testing the six named tests against six single-branch defects in `verify_scanner_image.py` (`mutation-test.out`):

| Mutant | Result |
|---|---|
| M1 drop resolved-digest check in `main()` | **SURVIVED** — 6 passed |
| M2 drop frozen-manifest SHA check in `main()` | **SURVIVED** — 6 passed |
| M3 drop OCI label comparison | KILLED (2 failed) |
| M4 drop artifact archive SHA check | KILLED (1 failed) |
| M5 drop inspect single-image shape guard | **SURVIVED** — 6 passed |
| M6 drop bundled image-tar checksum comparison | KILLED (1 failed) |

`main()` is never invoked by any test, so the two most load-bearing pins in the whole lane — the resolved image digest and the frozen release-manifest SHA — can be deleted outright and the suite still reports "6 passed". Four of the nine fail-closed branches have zero coverage. The acceptance scope asks for all fail-closed mismatch coverage to be reviewed; it is not yet there.

Current behaviour is correct (D2 is regression risk, not a live functional bug). `fail-closed-matrix.py` covers the missing branches and can be adapted.

### D3 — MEDIUM: the evidence document implies a binding it does not verify

One `status: PASS` covers both an `image` object and an `actions_artifact` object, but nothing ties the artifact's `scanner-edge-image.tar` to the verified GHCR digest. They originate in different workflows and different runs: `scanner-edge-image.yml` builds `backbond-scanner-live:<sha>` locally and `docker save`s it to a tar artifact — it never pushes to GHCR — while GHCR publishing is done by `sign-beaaa-19984.yml`. The bundled `.tar.sha256` lives inside the same zip, so it is self-referential; the only external anchor is `EXPECTED_ARTIFACT_SHA256`.

A reader of this evidence would reasonably conclude the audited tar is the verified image. That is not established. Either bind them (load the tar and compare config digest or OCI labels against the GHCR image) or state plainly in the evidence schema that the two are independently pinned and not cross-checked.

### D4 — MEDIUM: mutable action tags in an immutability lane

The candidate uses `actions/checkout@v6`, `docker/login-action@v3`, `actions/upload-artifact@v7`. The repo's own comparable security lane, `sign-beaaa-19984.yml`, pins by commit SHA:

```
uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
uses: docker/login-action@184bdaa0721073962dff0199f1fb9940f07167d1 # v3.5.0
```

A lane whose product is independent verification of an immutable image should not resolve its own tooling through mutable tags. This is a regression against established in-repo practice.

### D5 — MINOR: permission test is a source grep

`test_workflow_permissions_are_exactly_read_only` asserts `"write" not in text` over the whole file. It is a substring check, not a behavioural one: it would trip on the word "write" in an ordinary comment, and it parses only top-level `permissions`, so a job-level override would be caught by luck rather than by design.

## Live dispatch — genuinely unavailable to this seat

The acceptance scope requires a `workflow_dispatch` run proving the pinned GHCR and Actions-artifact checks. It cannot be performed here, for structural reasons rather than preference:

1. `workflow_dispatch` only becomes triggerable once the workflow file is on the default branch. `.github/workflows/scanner-image-readonly-verify.yml` is absent from `master` (`ae149230`). This is a GitHub platform rule.
2. Landing it on `master` is a merge. QA does not merge its own candidate — that is the separation this gate exists to enforce.
3. This environment holds no GitHub credential at all: `GITHUB_PAT_ARE_SCANNER`, `GITHUB_TOKEN` and `GH_TOKEN` are unset, and `git ls-remote origin` fails authentication.
4. Per BEAAA-16744, agent seats are blocked from GitHub Actions writes; unblocking is Founder/org-admin.

**Owner of step 1: the BEAAA-21512 assignee (`bc8396d5-dee6-4ad8-ae3f-1a2280088cb4`) / CTO.** D1 should be fixed before that merge, because the first dispatch is expected to fail on the artifact download as written.

No `APPROVED` is recorded. The verdict here rests on defects found by local execution and mutation testing, not on the absence of the dispatch.

## Reproduce

```
git worktree add --detach /tmp/qa21513 5d52651a473cada3c639f38c5bec1d6fc4933044
cd /tmp/qa21513 && python3 -m pytest -q tests/test_scanner_image_readonly_verify_beaaa21512.py
WT=/tmp/qa21513 python3 artifacts/BEAAA-21513/fail-closed-matrix.py
WT=/tmp/qa21513 python3 artifacts/BEAAA-21513/mutation-test-acceptance-suite.py
git show 7850289333ffc1de6eebea9c2201e0f1059c4c12:release/backbond-scanner-release.json | sha256sum
```

Artifacts in this directory: `fail-closed-matrix.py` / `.out`, `mutation-test-acceptance-suite.py`, `mutation-test.out`, `acceptance-pytest.out`.
