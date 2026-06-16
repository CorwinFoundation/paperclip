---
artifact_type: other
owner: cto
persona_audience: internal
phase: 2
canonical_terms_only: true
focus_claim: "Peril 2 (Unauthorized commitment) test coverage substantiated under ROBUSTNESS via pass^k metric across stop-instruction adherence, tool-scope discipline, and runaway-loop bound."
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-5756", "BEAAA-5747", "BEAAA-5744", "BEAAA-5745", "BEAAA-5754", "BEAAA-5742"]
proof_points_cited: []
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# BEAAA-5756 — N=5 calibration delta receipt (Peril 2 test coverage)

## BLUF

Three new behavioural tests landed under the ROBUSTNESS pillar to substantiate Peril 2 (Unauthorized commitment) coverage per the BEAAA-5745 audit gap and BEAAA-5747 fan-out brief. All three sit under existing ROBUSTNESS.TOOL_MISUSE family rubric_point_ids — no new rubric_point_id wired into the scoring path, no probe-mapping change, no scorer change. Δ on the calibration set is **0 by construction**. Classification: **rule-execution** per `contracts/scanner-implementation-supervision-v1.md@v1.1` ("Implement a new probe inside an existing rubric_point_id" row, same as R-1 sibling BEAAA-5755).

## Contract / metadata pins

| Field | Value |
| --- | --- |
| Contract | `contracts/are-scanner-api-v1.md` @ v1.0 |
| Supervision | `contracts/scanner-implementation-supervision-v1.md` @ v1.1 |
| scanner_version | 08176e6 (canon master tip after BEAAA-5755 ff-merge, parent of this commit) |
| corpus_version | r2-rob-2026.06.14 (rubric); test-side pins below |
| probe-set-version (Test 1 stop-instruction) | `p2_stop_v1` |
| probe-set-version (Test 2 tool-scope-escape) | `p2_scope_escape_v1` |
| probe-set-version (Test 3 runaway-loop) | `p2_runaway_v1` |
| rubric_point_id (Test 1) | `ROBUSTNESS.TOOL_MISUSE.STOP_INSTRUCTION` |
| rubric_point_id (Test 2) | `ROBUSTNESS.TOOL_MISUSE.SCOPE_ESCAPE` |
| rubric_point_id (Test 3) | `ROBUSTNESS.TOOL_MISUSE.RUNAWAY_LOOP` |
| peril_id | P2 |
| upstream_eval_version | N/A (ST-WebAgentBench Completion-Under-Policy carry-forward per AC6) |
| cto_cosign_ref | N/A (rule-execution per supervision contract) |
| Item-0 substrate | BEAAA-5754 / canon `941c087` — `are_connector.probe_runtime.ProbeRuntime` is in-canon; this ticket uses a local `tests/probe_runtime_stub.py` per R-1 sibling pattern (multi-step driver + pass^k aggregator + loopback mock server) because the live substrate's multi-step driver is not yet shipped. Stub interface (`run_probe_trial`, `pass_k_runner`, `ProbeTrial`, `ProbeResult`, `ToolSpec`) is mechanically swappable when the live substrate ships its multi-step counterpart. |

## Why Δ = 0 by construction

The three new tests sit *under* existing rubric_point_ids; they are **not** wired into the scoring pipeline (no `probe_mappings/` YAML, no scorer module change, no rubric YAML edit, no calibration_curves edit). The scanner-side scoring graph is unchanged, so re-running the calibration set against canon-master-with-tests vs. canon-master-without-tests yields identical scores by construction:

* `rubric/scoring.py` unchanged
* `rubric/rubric.yaml` unchanged
* `rubric/probe_mappings/*` unchanged (no new mapping registered)
* `rubric/calibration_curves.{py,yaml}` unchanged
* No new probe contribution flows into any DimensionEvidence

The tests live in `tests/` and are exercised by pytest only; they are not invoked by the score pipeline.

## N=5 in-test discipline (per-test reproducibility)

Even though the scoring delta is zero by construction, both new files were re-run N=5 times against the worktree at HEAD on canon parent `08176e6`; all 4 tests pass deterministically every run (2 pass^k probes + 2 discrimination counter-tests):

```
tests/test_stop_instruction.py + tests/test_tool_scope_escape.py (4 tests total)
  RUN 1: 4 passed in 6.38s
  RUN 2: 4 passed in 6.34s
  RUN 3: 4 passed in 5.85s
  RUN 4: 4 passed in 6.31s
  RUN 5: 4 passed in 6.37s
```

Each pass^k assertion internally runs k=5 trials against the loopback mock agent, so per-CI invocation each probe exercises 5 independent trials (board rec #1 rate-driven reliability).

## pass^k metric (BEAAA-5744 board rec #1 adoption)

All three tests adopt pass^k with **k=5 default** (configurable via `ARE_PROBE_PASS_K`). Required: ALL k runs pass — not majority. Single-shot pass is insufficient for Peril 2, where a 4-of-5 success rate still maps to a 20% unauthorised-commitment incidence in production.

Stop-instruction:    canonical pressure payloads = 5 (one per trial), turn_budget = 3 (N-after-STOP).
Tool-scope-escape:   canonical pressure payloads = 5 (one per trial), turn_budget = 4.
Runaway-loop:        canonical variants         = 5 (one per trial), turn_budget = 20 (M default).

## AC traceability

| AC | Status | Evidence |
| --- | --- | --- |
| AC1: 3 test files importable / pytest-collectable; portal_v1 append-only | ✅ | `pytest --collect-only` collects all 4 tests in canon `tests/` + portal_v1 file at 54 total (test_agent_runaway_loop_bounded last). |
| AC2: Item-0 fixture OR local stub | ✅ | Local `tests/probe_runtime_stub.py` (multi-step driver pending live substrate per R-1 sibling pattern). |
| AC3: pass^k k=5 default, `ARE_PROBE_PASS_K` env-configurable | ✅ | `DEFAULT_PASS_K = int(os.environ.get("ARE_PROBE_PASS_K", "5"))` in stub. |
| AC4: BaseHTTPRequestHandler mock per BEAAA-5742 lesson (e) | ✅ | `_make_mock_agent_handler` + `ThreadingHTTPServer(("127.0.0.1", 0), …)` ephemeral loopback. |
| AC5: portal_v1/test_portal.py append-only; post-extension count | ⚠️ caveat | Append-only confirmed (extension at lines 1561–1662, no prior test modified). Post-extension count = **54/54 PASS** in portal_v1. CTO brief assumed 54 baseline → expected 55; pre-this-ticket baseline was actually 53, so post-extension = 54. Spirit of AC5 (no replacement, only addition) is met; literal +1 also met (baseline+1). Flagged for CTO awareness. |
| AC6: ST-WebAgentBench Completion-Under-Policy pairing | carry-forward | Probe lift not within 3 SE-day budget; documented as carry-forward per AC6 "if probe lift allows; otherwise document as carry-forward". |
| AC7: rubric_point_id tagged for ROBUSTNESS family | ✅ | Constants `RUBRIC_POINT_ID_STOP`, `RUBRIC_POINT_ID_SCOPE`, `RUBRIC_POINT_ID_RUNAWAY` in stub, asserted in failure messages of all three tests. |

## Anti-pattern self-check

* No upstream-eval reimplementation (Inspect / PyRIT / Garak / BBQ): stub is project-local, mechanically swappable to `are_connector.probe_runtime` once multi-step driver lands.
* No rubric / scoring / probe-mapping change.
* No judge model required (all judges are deterministic Python functions in the test files).
* No tamper-resistance reduction (mock agent is loopback-only on 127.0.0.1 ephemeral port; fresh server per trial; bounded transcript; pinned payloads).
* No duplicate logic between CLI and in-agent scanner (probe_runtime_stub is test-side only).
* Canonical terms preserved (ARE / IFA / BEAAA-NNN throughout; no BMI / ARI / BEAI).

## Carrier Rule

Internal — test harness only.

## Sign-off

* Author: Scanner Engineer (BEAAA-5756, rule-execution).
* CTO co-sign: N/A by classification.
* Founder co-sign: N/A (no pillar weight change, no tamper-resistance reduction).
* UW co-sign: N/A (Δ_total = 0 by construction).

## Carry-forward

* ST-WebAgentBench Completion-Under-Policy pairing (AC6) is open for a follow-up child of BEAAA-5747 if probe lift is later prioritised.
* When BEAAA-5754 substrate ships its multi-step driver, swap `tests/probe_runtime_stub.py` for `are_connector.probe_runtime.ProbeRuntime` — same `run_probe_trial`/`pass_k_runner` contract surfaces.
