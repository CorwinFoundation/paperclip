---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-8330 restores the BackBond Scanner production customer-endpoint path for Syllable apichat without changing scoring rules or contract output shape."
contracts_referenced:
  - are-scanner-api-v1
  - scanner-implementation-supervision-v1
ticket_refs:
  - BEAAA-8330
  - BEAAA-8325
  - BEAAA-6035
proof_points_cited:
  - tests/integration/test_syllable_request_construction.py
  - tests/test_golden_agents.py
  - tests/test_golden_set_l2_gate.py
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted:
  - "Rule-execution under scanner-implementation-supervision-v1.md@v1.1 row `Customer onboarding harness for a new customer archetype` / `Refactor that doesn't touch behavior`; no scoring math, judge model, rubric point, pillar weight, or contract output shape changed."
---

# BEAAA-8330 N=5 Calibration Delta

BLUF: PASS. The Syllable apichat production `/scan` customer-endpoint leg now routes through a hardened two-step Syllable request shape; golden-set score replay remains unchanged.

## Required Scanner Metadata

| Field | Value |
|---|---|
| scanner_version | 0.4.2 |
| corpus_version | unchanged |
| probe-set-version | customer-agent walker v0.3; Syllable request construction only |
| rubric_point_id | P1.1, P2.1, P3.1, P4.1 walker-covered rows; no rubric change |
| N=5 calibration delta | 0 total movement by construction; five golden replay passes completed |
| cto_cosign_ref | not required for rule-execution; UW diff co-sign still requested by BEAAA-8330 acceptance |
| contract anchor | are-scanner-api-v1@v1.0; scanner-implementation-supervision-v1.md@v1.1 |

## Classification

Rule-execution. This is customer-endpoint request construction under an existing production walker path. It does not add or remove a rubric point, change scoring math, change judge models, change band cuts, alter evidence output shape, or reduce tamper resistance.

## Implementation Delta

`harness/customer_runner.py` now routes `apichat.syllable.cloud` to a Syllable-aware customer runner that sends:

- `Syllable-Client-Id-Key`
- `Syllable-Host-Key`
- `Origin`
- `Accept-Encoding: identity`

It performs `POST /api/v1/chats/start_session` followed by `POST /api/v1/chats/send_message` with `{session_id, message}`. Both calls use `ScopedEgressAllowlist`, `follow_redirects=False`, raw streaming, `_MAX_RESPONSE_BYTES`, and non-identity `Content-Encoding` rejection.

All non-Syllable endpoints remain on `_CustomerAgentHttpRunner`.

## Verification

Focused request-construction regression:

```text
pytest -q tests/integration/test_syllable_request_construction.py
4 passed
```

Customer-runner hardening regressions:

```text
pytest -q tests/integration/test_customer_runner_egress.py tests/integration/test_customer_runner_compression_bomb.py tests/integration/test_customer_runner_rejects_redirects.py tests/integration/test_customer_runner_size_cap.py
6 passed
```

Customer walker integration:

```text
pytest -q tests/integration/test_walker_customer_agent_v0_3.py
11 passed
```

N=5 golden replay:

```text
for i in 1 2 3 4 5; do pytest -q tests/test_golden_agents.py tests/test_golden_set_l2_gate.py || exit 1; done
19 passed, 18 subtests passed in each of 5 runs
```

Syntax check:

```text
python3 -m compileall -q harness/customer_runner.py tests/integration/test_syllable_request_construction.py
PASS
```

## Remaining Deployment Gate

The production replay script named in BEAAA-8330 acceptance, `scripts/beaaa5448_t0_prod_rescan.py`, is not present in this worktree. Local acceptance is complete; deployed `/scan` and `/scanner/v1/rescans` acceptance still needs to run from the production deployment workspace after merge/deploy.
