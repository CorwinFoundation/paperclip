---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-7234 adds offline BackBond Scanner peril probes while refusing to close without the required N=5 golden-set calibration gate."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-7234", "BEAAA-6200", "BEAAA-6970"]
proof_points_cited: ["Focused BEAAA-7234 pytest slice", "Existing golden-agent regression failure output"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["N=5 golden-set calibration blocked by pre-existing golden fixture missing-dim failures; do not close done until resolved."]
---

# BEAAA-7234 Progress Note

## BLUF

Rule-execution implementation landed locally for the offline money-movement wrapper and stop-instruction probe. The ticket is not shippable yet because the required N=5 golden-set calibration gate cannot run cleanly in the current canonical checkout.

## Classification

- Deliverable: probe implementation for CTO/Underwriter that adds offline money-movement and autonomy-bound stop-instruction evidence emitters within the existing BackBond Scanner contract envelope.
- Supervision row: `scanner-implementation-supervision-v1@v1.1`, rule-execution rows "Wrap an upstream eval framework" and "Implement a new probe inside an existing rubric_point_id".
- Contract anchor: `are-scanner-api-v1@v1.3.0`; output shape unchanged.
- `cto_cosign_ref`: not required for this local wrapper/probe implementation unless N=5 golden-set delta exceeds ±1 or rubric scoring activation is merged.

## Implemented Paths

- `evals/perils/money_movement/`
  - deterministic end-state verifier for payee, amount/currency, final ledger state, idempotency, and reversal handling.
  - pass^k harness with `k=5` default.
  - evidence row projection for `ROBUSTNESS.tool_misuse.money_movement.*`.
  - upstream pins recorded for:
    - `sierra-research/tau-bench@59a200c6d575d595120f1cb70fea53cef0632f6b`
    - `sierra-research/tau2-bench@5ebebbe827b455b3ed04fcb9294235c6ef4e5fd6`
- `probes/stop_instruction/v1/`
  - canonical five-pattern stop-signal set.
  - adversarial variant pattern for conflicting authority, permission-framed bypass, and continuation incentive.
  - deterministic checks for no actions, no tool calls, and explicit acknowledgement.
  - evidence row projection for `GOVERNANCE.autonomy_bound.refuse_to_stop.*`.
- `tests/test_beaaa7234_firewalls.py`
  - import firewall for `pricing`, `portal`, `tools.post-bind-ingest`, and `bands`.

## Verification

Passed:

```text
python3 -m pytest tests/test_money_movement_beaaa7234.py tests/test_stop_instruction_beaaa7234.py tests/test_beaaa7234_firewalls.py -q
6 passed in 0.30s
```

Blocked:

```text
python3 -m pytest tests/test_golden_agents.py -q
12 failed, 7 passed, 6 subtests passed in 0.78s
```

Failure signature: existing golden fixtures do not include already-active dimensions:

```text
ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL
ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER
ROBUSTNESS.PII.SCAN_TIME_DETECTION
GOVERNANCE.PII.HANDLING_ATTESTATION
```

This failure occurs before any BEAAA-7234 code is reached. No N=5 golden-set delta is claimed.

## Operational Metadata

- `scanner_version`: 0.4.1 unchanged.
- `corpus_version`: current golden fixtures under `tests/fixtures/golden_agents`; calibration blocked before delta computation.
- `probe-set-version`: `money-movement-passk@0.1.0`; `stop-instruction@1.0.0`.
- `rubric_point_id`: `ROBUSTNESS.tool_misuse.money_movement.*`; `GOVERNANCE.autonomy_bound.refuse_to_stop.*`.
- `N=5 calibration delta`: blocked; focused pass^k tests pass at `k=5`, but scanner golden-set N=5 is not complete.
- `upstream_eval_version`: tau-bench and tau2-bench pins listed above.
- `cto_cosign_ref`: not applicable to this unactivated rule-execution wrapper; required if rubric scoring activation or >±1 golden movement appears.
- `merged_sha_on_canon_master_at_close`: not available; do not close done until committed and merged/reachable from canonical master.

## FAL Audit

- Friction: the code emits deterministic evidence rows and avoids downstream pricing imports.
- Anxiety: the golden-set gate is failing from pre-existing fixture drift; closing done would breach supervision-v1.1.
- Leverage: the wrappers are isolated and can be activated after the golden fixture gap is repaired.

