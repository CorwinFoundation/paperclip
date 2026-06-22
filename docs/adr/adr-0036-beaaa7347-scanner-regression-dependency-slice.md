---
artifact_type: adr
owner: cto
persona_audience: internal
phase: 2
canonical_terms_only: true
focus_claim: "BEAAA-7347 preserves BackBond Scanner release discipline by making the scanner-regression N=5 gate install only the deterministic scoring dependencies it exercises."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-7347", "BEAAA-6190", "BEAAA-7345"]
proof_points_cited:
  - ".github/workflows/scanner-regression.yml"
  - "requirements-scanner-regression.txt"
  - "GitHub Actions scanner-regression run 27942781838"
classification: "rule-execution; CI dependency fix for an existing regression gate; no scoring math, rubric, judge, corpus, or contract output change"
scanner_version: "0.5.0"
corpus_version: "fixture-pinned per golden expected_score.json"
probe-set-version: "golden_agents_v1"
rubric_point_id: "release-wide/all active BackBond Scanner rubric points"
n5_calibration_delta: "PASS; total spread 0 and max pillar spread 0.0000 across live goldens B1/G1/G2/M1/M2/M3"
upstream_eval_version: "garak 0.10.0 and PyRIT 0.6.0 unchanged"
contract_output_shape_unchanged: true
non_score_moving: true
version: 1.0
prior_version: null
self_audit_passed: true
escalations_noted:
  - "No CTO co-sign required under scanner-implementation-supervision-v1@v1.1 rule-execution rows for smoke tests and non-behavioral refactors."
  - "The full-runtime garak/PyRIT datasets conflict remains a separate upstream-eval environment split issue; this ADR only scopes the deterministic scanner-regression job."
---

# ADR-0036 — BEAAA-7347 Scanner-Regression Dependency Slice

## BLUF

Decision: `scanner-regression` installs a dedicated CI dependency slice, `requirements-scanner-regression.txt`, instead of the full runtime `requirements.txt`.

Reason: the job exercises deterministic in-tree scoring, CLI, and tamper-resistance regression tests. It does not run live garak or PyRIT upstream evals. Installing both upstream frameworks in one strict pip transaction makes the gate fail before the N=5 evidence can run because garak `0.10.0` requires `datasets<2.17,>=2.14.6` and PyRIT `0.6.0` requires `datasets>=3.0.0`.

## Context

BEAAA-7345 surfaced that the BEAAA-6190 release-candidate branch was red in `scanner-regression`. The failing step was dependency resolution, not scoring. Reproduction in a clean virtualenv showed `pip install -r requirements.txt` fails with `ResolutionImpossible` on the garak/PyRIT `datasets` ranges.

The workflow then never reached:

- `tests/test_golden_agents.py`
- `tests/test_cli_runner_beaaa464.py`
- the P1 golden suites
- `tests/test_scanner_tamper_red_team.py`
- `scripts/beaaa6190_v05_rc_n5.py`

That means the release gate was red for an install-surface mismatch, not a BackBond Score regression.

## Decision

Create `requirements-scanner-regression.txt` with only the packages the deterministic gate needs:

- `PyYAML==6.0.2`
- `httpx==0.27.2`
- `pytest==9.0.3`

Update `.github/workflows/scanner-regression.yml` to use that file for pip cache keying and installation.

The upstream eval pins remain unchanged in `requirements.txt`:

- garak `0.10.0`
- PyRIT `0.6.0`

This is a CI dependency-slice decision, not an upstream eval version bump.

## Consequences

Positive: `scanner-regression` now reaches the actual deterministic tests and N=5 gate. GitHub Actions run `27942781838` completed successfully on commit `992395c476c9ac50f7da3e713abb84711f753b48`.

Neutral: no scoring code, rubric file, judge model, corpus fixture, probe mapping, or contract output shape changed. Score delta is zero by construction and confirmed by the N=5 gate.

Watch: the full runtime environment still cannot install garak and PyRIT together under strict pip resolution. That should be handled as a separate upstream-eval environment split, because those frameworks have incompatible transitive `datasets` requirements. This ADR does not claim the full runtime dependency graph is internally consistent.

## Rejected alternatives

1. Use pip's legacy resolver in `scanner-regression`.

Rejected because it would mask the incompatible `datasets` pins and make the release gate green by accepting an unsupported upstream-eval environment.

2. Change garak or PyRIT versions.

Rejected because an upstream eval version bump is governed by `scanner-implementation-supervision-v1@v1.1` and requires a fresh N=5 calibration delta. The scanner-regression job does not need either live upstream framework to prove the BEAAA-6190 release-candidate gate.

3. Install the full runtime and skip dependency checks.

Rejected because the install step is part of reproducibility discipline. A release gate should fail loudly when it needs the full runtime and the full runtime is inconsistent; this specific gate does not need that full runtime.

## Verification

- Original failure reproduced: `pip install -r requirements.txt` fails with `ResolutionImpossible` on garak/PyRIT `datasets`.
- Clean Python 3.12 virtualenv: `pip install -r requirements-scanner-regression.txt` passed.
- Workflow test command passed locally: `67 passed, 20 subtests passed`.
- N=5 gate passed locally: `scripts/beaaa6190_v05_rc_n5.py`.
- GitHub Actions `scanner-regression` run `27942781838` passed on commit `992395c476c9ac50f7da3e713abb84711f753b48`.

## Rollback

Revert commit `992395c476c9ac50f7da3e713abb84711f753b48` and remove `requirements-scanner-regression.txt`. Expected rollback effect: `scanner-regression` returns to strict full-runtime installation and again fails before tests because garak and PyRIT require incompatible `datasets` versions.
