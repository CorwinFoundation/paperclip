---
issue: BEAAA-16634
parent: BEAAA-16598
candidate_sha: 737721965756e83ec6e7acfb1c61206c64c49ab9
candidate_sha_full: 737721965756e83ec6e7acfb1c61206c64c49ab9
branch: worktree-beaaa-16598-deploy-path
rejected_sha_do_not_resubmit: 5fda1a2ca8e1b39f0e518dddb141f763c135717f
scanner_version: "0.5.0"
corpus_version: "0.1.0"
probe_set_version: "n/a — no probe/rubric/judge code touched"
rubric_point_id: "n/a — deploy-path infrastructure, no rubric surface"
n5_calibration_delta: "Δ0 by construction (test-only + doc + relocation of retired non-invoked scripts; no scoring path)"
cto_cosign_ref: "n/a — rule-execution (deploy infra), no rubric/weight/judge/contract change"
contract_version: are-scanner-api-v1@v1.0 (unchanged)
persona_audience: internal
---

# BEAAA-16634 — r3-family remediation of BEAAA-16598 QA blockers

**BLUF.** The three QA rejections of BEAAA-16598 candidate
`5fda1a2ca8e1b39f0e518dddb141f763c135717f` (gates 16621 / 16622 / 16623) are
remediated. Fresh candidate SHA is **`737721965756e83ec6e7acfb1c61206c64c49ab9`**
(≠ the rejected SHA) on `origin/worktree-beaaa-16598-deploy-path`. Focused
BEAAA-16598 suite + the four ADR-0057 deploy regression suites: **77 passed**.
Canonical `tests/` suite: **2706 passed, 6 pre-existing failures** (all
release-workflow / sigstore, untouched by this diff). No scoring/rubric/corpus/
OpenAPI/API-contract change; scanner_version 0.5.0, corpus_version 0.1.0; Δ0 by
construction.

## Candidate

- Branch: `worktree-beaaa-16598-deploy-path`
- Candidate (test on this exact SHA): `737721965756e83ec6e7acfb1c61206c64c49ab9`
- DO NOT re-review rejected SHA `5fda1a2ca8e1b39f0e518dddb141f763c135717f`.
- Commits since the rejected r2 tree (full remediation = `git diff 5fda1a2..7377219`):
  - `3b7bcd7` r3 — retire the remaining two orphaned ADR-0057 siblings
    (`acceptance_checks.sh`, `post_deploy_record.py`) to `scripts/deploy/history/`;
    document all four in `history/README.md`.
  - `579cbee` r4 — repoint the three ADR-0057 regression tests to the `history/`
    paths so the script moves no longer raise `FileNotFoundError`.
  - `7377219` r5 — broaden the active-deploy-path sweep to the full retired-token
    set across every active executable deploy artifact.

## Blocker → fix mapping

### B1 — active-vs-history contradiction (16621 / 16623)
`acceptance_checks.sh` and `post_deploy_record.py` were left LIVE and orphaned in
the active path in r2 while docs claimed the ADR-0057 group was retired.
**Fix (r3):** both moved to `scripts/deploy/history/` via `git mv`, joining
`box_deploy.sh` + `operator_push.sh`. `history/README.md` now documents all four
with their ADR-0057 role and what supersedes each. No active deploy script
carries a dangling `operator_push.sh` reference (enforced by the sweep, below).

Active `scripts/deploy/` after r3: `scanner-deploy.sh`, `dev_deploy.sh`,
`deploy_agent_acceptance.py`, `release_identity_gate.py`, `stage_gate_config.py`.
Retired under `history/`: `box_deploy.sh`, `operator_push.sh`,
`acceptance_checks.sh`, `post_deploy_record.py`, `README.md`.

### B2 — regression: pre-existing deploy tests FileNotFoundError (16622)
The script moves broke three tests that reference the retired scripts by path.
**Fix (r4):** repointed to `history/` with a truthful `# BEAAA-16598: ADR-0057
scripts moved to history/` header on each:
- `tests/test_deploy_reversible_cutover_beaaa13362.py:11-12` → `history/box_deploy.sh`, `history/operator_push.sh`
- `tests/test_deploy_acceptance_checks_beaaa13362.py:9` → `history/acceptance_checks.sh`
- `tests/test_deploy_acceptance_wiring_seq11.py:99-100` → `history/{acceptance_checks,operator_push}.sh`

These remain honest coverage: they verify the retired-but-preserved artifacts
still behave as ADR-0057 documented; they no longer assert anything about the
active deploy path.

### B3 — sweep broadening (16621 B2 / 16623)
The r2 sweep globbed `*.sh` only and dropped the `operator_push` token, so it
passed green-but-wrong while orphaned scripts sat in the active path.
**Fix (r5)** in `tests/test_scanner_deploy_beaaa16598.py`:
- `RETIRED_MECHANISM_TOKENS` = `box_deploy`, `operator_push`, `operator-mediated`,
  `/etc/are-scanner`, `/version`, `docker pull` — applied to every active script.
- `_active_scripts()` now covers `*.sh`, `*.py`, **and** any executable-bit file
  (extensionless helpers); `history/` excluded (non-recursive `iterdir`); asserts
  it found scripts (guards a mis-scoped glob).
- GHCR live-container pulls/swaps forbidden on every active script **except** the
  `dev_deploy.sh` provenance PUSH (build side, never a live-box swap); the box
  helper `scanner-deploy.sh` keeps its absolute bare-`ghcr` ban.

### B4 — sweep must fail-before / pass-after
Verified against a detached worktree at the rejected r2 SHA:
- On `5fda1a2` the expanded sweep **FAILS**
  (`test_active_deploy_scripts_have_no_retired_mechanism_references` +
  `test_retired_scripts_preserved_under_history`): the orphaned active
  `acceptance_checks.sh` carries `operator_push` ×2 and `operator-mediated` ×1;
  `post_deploy_record.py` carries `operator-mediated` ×1.
- On `7377219` the sweep **PASSES**.

## Acceptance-criteria evidence

| Criterion | Result |
|---|---|
| Fresh SHA ≠ 5fda1a2c | `737721965756e83ec6e7acfb1c61206c64c49ab9` ✅ |
| Focused BEAAA-16598 tests pass | `test_scanner_deploy_beaaa16598.py` 18/18 ✅ |
| reversible_cutover + acceptance_wiring pass | repointed to history/, pass ✅ |
| Scoped active sweep → zero forbidden refs outside history/ | passes on tip ✅ |
| No score-moving changes | r2→tip = 7 files, all deploy scripts/tests/docs; grep of rubric/corpus/scoring/openapi/api/probe/judge = NONE ✅ |
| scanner 0.5.0 / corpus 0.1.0 / Δ0 | unchanged; Δ0 by construction ✅ |
| Exactly one fresh QA gate | one gate created for `7377219`; no re-review of `5fda1a2` ✅ |

## Full change set (`git diff --stat 5fda1a2..7377219`)
```
 scripts/deploy/history/README.md                   | 16 +++--
 scripts/deploy/{ => history}/acceptance_checks.sh  |  0
 scripts/deploy/{ => history}/post_deploy_record.py |  0
 tests/test_deploy_acceptance_checks_beaaa13362.py  |  3 +-
 tests/test_deploy_acceptance_wiring_seq11.py       |  5 +-
 tests/test_deploy_reversible_cutover_beaaa13362.py |  5 +-
 tests/test_scanner_deploy_beaaa16598.py            | 82 +++++++++++++++++----
 7 files changed, 88 insertions(+), 23 deletions(-)
```
(`box_deploy.sh` + `operator_push.sh` were already relocated in r2 itself.)

## Pre-existing failures (out of scope, do not block this gate)
Canonical `tests/` run on the candidate: **2706 passed, 6 failed, 7 skipped,
2 xfailed, 197 subtests passed**. The 6 failures are all release-workflow /
sigstore / certificate-identity tests, present on the r1 base `3adc5feb`,
untouched by this deploy diff:
- `test_canonical_test_registry_beaaa13762.py::...test_beaaa13992_active_rows_count_only_target_bound_execution_evidence`
- `test_release_identity_gate_beaaa16095.py::test_workflow_hard_codes_master_certificate_identity`
- `test_scanner_edge_release_sequences_beaaa14583.py::test_manual_release_sequence_accepts_unbounded_positive_integers`
- `test_scanner_edge_release_sequences_beaaa14583.py::test_release_candidate_contains_verified_sigstore_bundle_hash`
- `test_scanner_edge_release_sequences_beaaa14583.py::test_only_manual_runs_publish_candidate_evidence_attachment`
- `test_scanner_edge_release_workflow_beaaa13989.py::test_current_release_workflow_emits_offline_verifiable_sigstore_bundle`

## Repro for QA
```
git fetch origin worktree-beaaa-16598-deploy-path
git checkout 737721965756e83ec6e7acfb1c61206c64c49ab9
# focused deploy suite (expect 77 passed)
python3 -m pytest tests/test_scanner_deploy_beaaa16598.py \
  tests/test_deploy_reversible_cutover_beaaa13362.py \
  tests/test_deploy_acceptance_wiring_seq11.py \
  tests/test_deploy_acceptance_checks_beaaa13362.py \
  tests/test_backbond_deploy_agent_beaaa13989.py \
  tests/test_live_edge_networking_seq11.py -q
# fail-before proof: same test file against rejected r2 (expect sweep FAIL)
git worktree add --detach /tmp/r2 5fda1a2ca8e1b39f0e518dddb141f763c135717f
cp tests/test_scanner_deploy_beaaa16598.py /tmp/r2/tests/
( cd /tmp/r2 && python3 -m pytest \
  tests/test_scanner_deploy_beaaa16598.py::test_active_deploy_scripts_have_no_retired_mechanism_references -q )
git worktree remove --force /tmp/r2
```

Promotion note: QA exact-SHA `APPROVED` is mandatory before merge; live/shared
promotion additionally requires Founder sign-off (BEAAA-15373 governance).
No live/shared promotion is in scope for this issue.
