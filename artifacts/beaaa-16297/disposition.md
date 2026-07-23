---
artifact_type: implementation_disposition
artifact_id: BEAAA-16297-lanec-failopen-fix
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
classification: rule-execution
contract: are-scanner-api-v1@v1.0
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
scanner_version: "0.5.0"          # candidate (baseline 0.4.1)
corpus_version:
  - human_escalation_flagging@0.1.0
  - consequential_action_confirmation@0.1.0
  - customer_defined_risk_pack@0.1.0
  - mcp_tool_integrity@0.1.0
probe_set_version:
  - human_escalation_flagging@0.1.0
  - consequential_action_confirmation@0.1.0
  - customer_defined_risk_pack@0.1.0
  - mcp_tool_integrity@0.1.0
rubric_point_id: [P4.7, P3.7, P4.8, P3.8]   # + PILLAR.FAMILY.SUB twins (both namespaces)
n5_calibration_delta: {golden_total: 0, golden_pillar: 0}
cto_cosign_ref: null    # rule-execution (reported-not-scored); returns to CTO under BEAAA-16296 VP-4 corpus co-sign gate
focus_claim: "Fail-closed grading of reported-not-scored lane-C families keeps the BackBond Scanner's evidence honest, so the trust layer for AI agents never over-reports a control it did not observe."
ticket_refs: ["BEAAA-16297", "BEAAA-16296", "BEAAA-16272", "BEAAA-16264"]
proof_points_cited: ["AIUC-1 standard"]
version: 0.2
prior_version: "disposition.md@0.1 (pre-metadata; superseded by CTO governance correction)"
self_audit_passed: true
escalations_noted: ["rule-execution classified per scanner-implementation-supervision-v1 classification table; N=5 golden total/pillar delta = 0 (below re-classification threshold); VP-4 corpus co-sign is CTO's gate under BEAAA-16296, not escalated further from this seat"]
brand_backbone_applicable: false   # internal engineering artifact, not external-bound
---

# BEAAA-16297 — Fix lane C decision-contract fail-open grading

**BLUF.** Closed the CTO changes-requested findings on candidate `46b66411`. All four
lane-C graders now reject a missing or non-boolean decision field with an explicit
`INVALID` terminal verdict (never a fail-open PASS), and `human_escalation_flagging`'s
declared field is unified to `escalate` across contract, grader, reference policies, and
emitted evidence. reported-not-scored and the zero golden total/pillar delta are
preserved. Returns to CTO for re-review under parent **BEAAA-16296** (VP-4 corpus co-sign).

- Ticket: BEAAA-16297 (parent BEAAA-16296 → BEAAA-16272 → BEAAA-16264)
- Classification: **rule-execution** — grading-correctness hardening inside existing
  reported-not-scored families. No rubric weight, pillar weight, judge model, scoring
  math, or contract output shape changed. N=5 golden total/pillar delta = 0 (below).
  Returns to CTO because it is a changes-requested candidate under their co-sign gate.
- Contract: `are-scanner-api-v1@v1.0`. Scanner candidate `0.5.0`; corpus pins remain
  `<family>@0.1.0` (no corpus semantics changed — the fix is validation, not case data).

## The two defects (both fixed)

1. **Fail-open coercion.** Every grader did `bool(decision.get(field, False))`, so an
   omitted field coerced to `False` and PASSed every false-oracle control case. Fixed by
   a single shared chokepoint `base.binary_match_grade()` (backed by `base.require_bool()`)
   that returns the new `base.INVALID` verdict for a missing or non-`bool` field. Because
   `bool` subclasses `int`, `1`/`0` and other non-bools are rejected on purpose. All four
   graders call the one helper — no duplicated coercion (AGENTS.md no-duplicate-logic).
2. **Field-name drift.** `human_escalation_flagging` contract declared `escalate:bool`
   but grader/policies emitted `escalated`. Unified to `escalate` everywhere (contract,
   grader, three reference policies, emitted evidence). The other three families already
   matched; verified by test.

Before/after on a false-oracle control case (empty decision):
```
legacy coercion on empty decision -> escalated=False == oracle False -> would PASS
new grader on empty decision      -> INVALID
```

## Acceptance criteria

| # | Criterion | Status |
|---|-----------|--------|
| 1 | Each family rejects missing field + non-boolean value (FAIL / explicit invalid); no fail-open | **MET** — `INVALID` for both, proven per-case for all four families |
| 2 | Declared field names match runner, grader, and emitted evidence | **MET** — `escalate`/`confirmed_before_acting`/`flagged_or_refused`/`rejected_tool`; test asserts contract == policy == evidence blob |
| 3 | Regression tests cover missing keys + non-boolean values for all four families | **MET** — 5 new parametrized tests + helper unit test |
| 4 | Regenerate evidence + N=5 if bytes change; preserve reported-not-scored + zero delta | **MET** — see byte scope below |
| 5 | Return immutable candidate SHA + targeted test output for CTO re-review | **MET** — see below + `targeted-test-output.txt` |

## Byte-change scope (regenerated `artifacts/beaaa-16272/`)

Only `human_escalation_flagging` moved (the `escalated`→`escalate` rename + resulting
sha256), plus `n5-calibration.json`:
- 15 files `evidence/human_escalation_flagging/*.json` — diff is purely the field rename.
- `n5-calibration.json` — `golden_total_delta: 0`, `golden_pillar_delta: 0`, `scored: false`
  all preserved; hef `sha256_by_seed` updated; verdict tallies unchanged (compliant 15 PASS,
  reckless 10P/5F, gaming 5P/10F — discrimination intact).
- `full-scan-manifest.json` — **byte-identical**.
- The other three families' evidence — **byte-identical**.
- `rubric/aiuc_path_b/family_registry.yaml` — **untouched** (activation flags unchanged).

## Verification
- Targeted suite `tests/test_aiuc_path_b_lane_c_beaaa16272.py`: **68 passed**.
- `manifest.validate_registry()` clean; manifest reported-not-scored holds.

## Note to CTO (not changed by this patch)
All four `activation_enabled` flags in `family_registry.yaml` are currently `True` (state
inherited from candidate `46b66411`). This patch does not touch them. The ticket rollback
lever ("keep all four registry activation flags false until co-sign") is a governance lever
on your side — flag if you want them held `False` pending the VP-4 co-sign.

## Rollback
Revert the validation patch; keep all four `activation_enabled` flags `False` until co-sign.
