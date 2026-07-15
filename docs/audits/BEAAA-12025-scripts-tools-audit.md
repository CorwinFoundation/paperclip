# BEAAA-12025 — `scripts/` + `tools/` periodic audit (inventory + archive pass 1)

**BLUF.** Inventoried `scripts/` (80 tracked `.py`/`.sh` + `agent_priors/` data) and `tools/`
(~15 subsystems). Classified each as **(a) load-bearing**, **(b) still-useful ad-hoc**, or
**(c) obsolete/one-off**. Pass 1 archives **21** unambiguously-obsolete, zero-reference, per-ticket
one-off scripts under `scripts/archive/` via `git mv` (history preserved). **No** CI, release-gate,
verifier, test, harness, runbook, ADR, or reproducibility-procedure file was touched. Golden gate
green (7 passed / 18 subtests), full suite still collects (2204 tests, no import errors). Golden
**Δ0 by construction** — a pure move of unreferenced files cannot change scoring.

- Ticket: BEAAA-12025 (parent BEAAA-12018 item #4 — third-party code-review housekeeping, LOW/backlog).
- Classification: **rule-execution** per `contracts/scanner-implementation-supervision-v1.md`
  (no rubric point, weight, judge model, scoring math, or tamper posture touched; contract shape
  unchanged). Anchored to `contracts/are-scanner-api-v1.md` — no contract delta.
- Base: `origin/master` @ `8201774`. Branch: `beaaa-12025-scripts-audit`.

## Method

For each script, references were located across the whole tracked tree with **both** its filename
(`foo.py`) **and** its Python module name (`foo`, as used by `from scripts import foo`), excluding
`artifacts/` (run outputs) and the file itself. The module-name form matters: several one-off
scripts are imported by a sibling `tests/test_*.py` without the `.py` suffix and are therefore
load-bearing for the test suite even though a filename-only grep shows them unreferenced.

**Archive criterion (pass 1, zero-breakage):** archive iff the script (1) matches the per-ticket
one-off pattern `beaaa<digits>_*.py` **and** (2) has zero references anywhere outside `artifacts/`
(by filename *and* module name). Everything else was kept.

## `scripts/` classification

### (a) Load-bearing — DO NOT archive without CTO sign-off
Referenced by CI (`.github/workflows/`), release gates, verifiers, the test suite, the live
`harness/`, runbooks, or ADR/cosign reproduction steps.

- **CI / release gates / verifiers:** `release_gate_n5.py`, `release_gate_semantic_invariance.py`,
  `release_gate_ws1b_n5.py`, `release_gate_beaaa11864_rotation_on_n5.py`,
  `beaaa6190_v05_rc_n5.py` (wired into `scanner-regression.yml`), `check_no_apiedge_score.py`,
  `check_v1_release_gate.py`, `verify_eval_ledger.py`, `verify-brand-backbone.sh`, `push_release.sh`.
- **Test / harness / runbook referenced:** `path_a_baselines.py`, `render_card.py`,
  `score_p4_attestation.py`, `path_b_endpoints.py`, `beaaa2182_a_calibration.py`,
  `beaaa2182_a_subprocess_end_to_end.py`, `beaaa5414_track_b_calibration.py`,
  `dry_run_p2_atticus.py`, `scan_lawconnect_atticus.py`, `scan_intercom_fin.py`,
  `beaaa334_wall_regress.py` (runbook), `beaaa759_l2_bias_pack_run.py` (ADR),
  and the N=5 calibration one-offs still cited by `tools/calibration-deltas/` provenance
  (`beaaa5485/5528/5532/5552/796_n5_calibration.py`, `beaaa8127_ops_live_fire.py`, etc.).
- **Test-imported one-offs (kept despite filename-grep showing unreferenced):**
  `beaaa758_l2_bias_pack_run.py`, `beaaa8125_tool_misuse_live_fire.py`,
  `beaaa8142_tool_misuse_real_agent_live_fire.py`, `beaaa8225_consolidated_full_coverage.py`,
  `beaaa8389_rasa_consolidated_grade_card.py`.

### (b) Still-useful ad-hoc / general utility — KEPT this pass (candidate for a later pass)
Currently unreferenced but plausibly general or reproducibility-adjacent; not ticket-scoped one-offs.
Left in place pending a future pass or CTO judgement rather than archived blindly:
`fetch_bbq.py`, `fetch_donotanswer.py` (upstream corpus fetchers — reproducibility),
`oss_evidence_pack_v1.py`, `verify_oss_evidence_pack_n5.py` (OSS evidence procedure),
`measured_leaderboard.py`, `score_real_agents.py`, `path_a_extended.py`, `path_a_v2_1.py`,
`poc_real_score.py`, `capture_fin_endpoint.py`, `capture_fin_v2.py`,
plus the `scan_*` / `dry_run_*` demo-scan family members.
`agent_priors/` (23 JSON + README) is **data**, not scripts — out of scope, kept.

### (c) Obsolete/one-off — ARCHIVED this pass (21)
Per-ticket, single-use, zero-reference (filename + module name). Moved to `scripts/archive/`
(see `scripts/archive/README.md` for the full table + restore instructions):
beaaa1277, beaaa152 (×2), beaaa198, beaaa206, beaaa209, beaaa2175, beaaa3009, beaaa3054 (×2),
beaaa334_n5_proxy_on, beaaa378, beaaa387, beaaa419, beaaa484, beaaa539, beaaa545, beaaa553 (×2),
beaaa675, beaaa749.

## `tools/` classification (inventory only — nothing moved this pass)

`tools/` is dominated by **load-bearing subsystems**, not loose one-offs, so no archive action taken:

- **(a) load-bearing:** `are-collector/` (Go collector; own release workflow
  `are-collector-release.yml` + `pr-unit-tests.yml`), `are-connector/` (Python connector; unit-tested
  in `pr-unit-tests.yml`), `calibration-deltas/` (127 dated N=5 provenance artifacts — the
  reproducibility ledger, **do not touch**), `backbond-scanner` + `backbond-scanner-fixtures/`
  (CLI + fixture), `portal_v1/`, `staging-endpoint-fixture/`, `connector-static-attest/`
  (all CI-referenced), `oss-export/` (BEAAA-11860 clean-export pipeline), `evidence-store/`,
  `post-bind-ingest/`, `pen-tests/`, `log-tracker/`, `founder-digest/`, `trust_page/`.
- **(b) ad-hoc, kept:** `regen_sample_envelope_r2_bias.py`, `regen_sample_envelope_r2_rob.py`
  (sample-envelope regenerators), the five `are-scan-smoke-*.md` receipts (historical evidence —
  could move to an evidence archive in a later pass; left for now).
- **(c) obsolete:** none identified with confidence this pass.

**Recommendation for a future pass (needs CTO judgement, not zero-breakage):** consider relocating
historical `tools/are-scan-smoke-*.md` receipts and possibly consolidating category-(b) `scripts/`
utilities. Deferred — out of scope for this rule-execution pass.

## Verification

- Archive criterion re-verified at `origin/master`: 21/21 zero-reference by filename + module name.
- `pytest --collect-only tests/` → 2204 tests collected, **no import errors** after the moves.
- `pytest tests/test_golden_agents.py` → **7 passed, 18 subtests passed** (canonical golden gate).
- Golden **Δ0** — pure `git mv` of unreferenced files; scoring paths byte-unchanged.

## Metadata
- scanner_version: unchanged (no scoring/CLI change)
- corpus_version: unchanged
- probe-set-version: unchanged
- rubric_point_id: N/A (no rubric point touched)
- N=5 calibration delta on golden set: **Δ0 (structural)** — golden gate 7p/18sub green, files moved are unreferenced by any scoring path
- cto_cosign_ref: N/A (rule-execution)
- contract: `are-scanner-api-v1.md` — no delta
