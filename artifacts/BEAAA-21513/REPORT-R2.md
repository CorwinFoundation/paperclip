# BEAAA-21513 R2 — QA report: read-only Scanner image verification

**Verdict: APPROVED — static layer only, scoped to this SHA.**

`SUPERSEDED: 5d52651a473cada3c639f38c5bec1d6fc4933044 -> e5b48c648f75882c47a985bf08b09a7f6028255b`

- Candidate: `e5b48c648f75882c47a985bf08b09a7f6028255b`, tip of `agent/beaaa-21512-readonly-verify`
- Tested 2026-08-17 in a detached worktree at the exact SHA
- Scope ruling: BEAAA-23837, CTO Option A, co-signed (`835cf89e`) — grant `actions: read`, retain the Actions-artifact binding
- Default branch: canonical `master` = `d361d063bbb597fa99fa4ef6ef8bbaccc1bcc329`; the workflow is **still absent** from it

## Provenance

The candidate was not in my clone. I located it at the canonical company repo `companies/.../projects/are-scanner` and fetched the exact object rather than trusting a description of it.

```
e5b48c64 BEAAA-21512 apply CTO Option A ruling: grant actions:read
a0153907 BEAAA-21512 close QA defects D2-D5 on read-only verification lane
5d52651a BEAAA-21512 add read-only image verification   <- the SHA I tested in R1
```

`git merge-base --is-ancestor 5d52651a e5b48c6` → true. Straight-line; nothing was rebased under my R1 verdict. The CEO's ancestry claim checks out.

**Not verified:** the push to `origin` (`CorwinFoundation/are-scanner`). This seat holds no GitHub credential — `git ls-remote origin` fails authentication from both my clone and the canonical repo. I can attest the object exists in the canonical company repo; I cannot attest it is on GitHub.

## R1 defects — all five closed

| Defect | Fix at `e5b48c6` | How I verified it, independently |
|---|---|---|
| **D1** `actions: none` → artifact REST 403 | `actions: read` added; exactly three scopes | Parsed YAML: `{contents: read, packages: read, actions: read}`, all values `read`, no job-level override. Upheld by CTO ruling, not overruled. |
| **D2** 3/6 mutants survived; `main()` untested | Suite rewritten, 6 → 18 tests, driven through the real `main()` | **Re-ran my own R1 mutant set unchanged: 6/6 now KILLED** (was 3/6 surviving). Not their mutants — mine. |
| **D3** evidence implied an unverified binding | Schema `@2` + explicit `binding_model.image_and_actions_artifact_cross_checked: false` with a prose note | Both the seed step and the PASS path emit `@2`; my matrix dumped the live PASS document and the disclaimer is present. |
| **D4** mutable action tags | All three pinned by 40-hex SHA | **Verified each SHA against upstream**: `ea165f8d…` is exactly tag `v4.6.2`/`v4` of `actions/upload-artifact`; `11bd7190…# v4.2.2` matches this repo's own `sign-beaaa-19984.yml` precedent. Version comments are accurate, not decorative. |
| **D5** permission test was a source grep | Structural YAML checks: exact dict, all-`read`, no job override, recursive string walk, `${{ }}` token allowlist `<= {github.token}` | Read the test source; the `"write" not in text` substring assertion is gone. |

## Independent verification performed

- `pytest -q tests/test_scanner_image_readonly_verify_beaaa21512.py` → **18 passed**.
- **My R1 mutation set, unmodified: 6/6 killed** (`mutation-test-r2.out`). This is the load-bearing check — the producer's suite now catches the exact defects it previously missed, measured with the same probe that caught them.
- **My R1 fail-closed matrix: 9/9** against the new script (`fail-closed-matrix-r2.out`), including the self-consistent forged artifact.
- **Reproduced the producer's own `artifacts/BEAAA-21512/mutation_check.py`: 11/11 killed, 0 survivors, exit 0**, and confirmed it restores the tree afterwards. Their recorded `mutation-test.out` matches what I got line for line — the receipt is honest. Their W1–W5 workflow mutants (widen scope, job-level override, mutable tag, inject secret, drop `actions: read`) are well chosen, not cherry-picked.
- Immutable pins are byte-unchanged from `5d52651a`: no `EXPECTED_*`, `SOURCE_*` or `IMAGE_*` value drifted.
- `EXPECTED_MANIFEST_SHA256` `2417a7b2…57ddc` recomputed from `7850289…:release/backbond-scanner-release.json` → exact match, still genuine.
- Trigger surface is `workflow_dispatch` only. No SSH, cosign, `secrets.`, `id-token`, write scope, deploy or production access anywhere in the file.

## Non-blocking observations

I ran ten mutants outside the producer's set, targeting the workflow's shell and trigger semantics rather than the Python logic. Five survived (`novel-mutants.out`). **The candidate is correct on all five** — these are regression-protection gaps, not live defects, and none of them changes the verdict.

| Mutant | Survived | Risk if it regressed later |
|---|---|---|
| O1 (N5) trigger widened to `pull_request_target` | 18 passed | verifier auto-runs in untrusted PR context |
| O2 (N6) fail-closed evidence seed step disabled | 18 passed | early failure leaves no evidence; upload is `if-no-files-found: warn`, so a failed run reports *no* document rather than `FAIL` |
| O3 (N7) `\|\| true` appended to the artifact metadata download | 18 passed | swallows an artifact API failure — the exact step D1 was about |
| O4 (N8) `--fail-with-body` dropped from the zip download | 18 passed | a 403/404 body gets saved as `source-artifact.zip` instead of failing |
| O5 (N10) evidence upload `if: always()` → `if: success()` | 18 passed | the FAIL document is never uploaded |

**O2 is a coverage regression against the SHA I tested in R1.** The old suite asserted `"Initialize fail-closed evidence" in text`; the rewrite dropped that assertion without replacing it. Worth restoring deliberately.

Implementation note for whoever picks these up: PyYAML parses a bare `on:` key as boolean `True` (YAML 1.1), so a trigger assertion must key on `True` or read raw text — which is likely why the trigger went unasserted.

## What this approval does NOT cover

The live `workflow_dispatch` layer is unverified and explicitly outside this verdict:

- the pinned GHCR tag resolving to `EXPECTED_DIGEST`, and the OCI labels on the real image
- the real Actions artifact download, including **whether `actions: read` in fact suffices** — my D1 diagnosis and its fix remain reasoning from GitHub's permission model plus the private-repo evidence, executed zero times
- whether source run `30672001992` / artifact `8809120470` are still within retention

`workflow_dispatch` only becomes triggerable once the file is on the default branch, and it is not. Remaining gap: **workflow absent from default branch. Owner: BEAAA-21512 assignee `bc8396d5` / CTO. Action: land the candidate on `master`.** The first dispatch after that merge is the evidence this gate still needs; I will record it here on the same gate.

## Reproduce

```
git fetch <canonical-repo> e5b48c648f75882c47a985bf08b09a7f6028255b
git worktree add --detach /tmp/qa21513r2 e5b48c648f75882c47a985bf08b09a7f6028255b
cd /tmp/qa21513r2 && python3 -m pytest -q tests/test_scanner_image_readonly_verify_beaaa21512.py
python3 artifacts/BEAAA-21512/mutation_check.py
WT=/tmp/qa21513r2 python3 artifacts/BEAAA-21513/fail-closed-matrix.py
WT=/tmp/qa21513r2 python3 artifacts/BEAAA-21513/mutation-test-acceptance-suite.py
python3 artifacts/BEAAA-21513/novel-mutants.py
```
