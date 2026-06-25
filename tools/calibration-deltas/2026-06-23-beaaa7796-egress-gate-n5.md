---
artifact_type: other
owner: cto
persona_audience: internal
phase: 2
canonical_terms_only: true
focus_claim: "BEAAA-7796 adds a local-only outbound monitor egress gate so BackBond can prove minimized post-bind artifacts leave the customer boundary only through one contract-checked path."
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1", "monitor-data-contract-v0.1"]
ticket_refs: ["BEAAA-7796", "BEAAA-7794", "BEAAA-7768"]
proof_points_cited: ["monitor-data-contract-v0.1 invariant from BEAAA-7796 heartbeat context", "L1 golden N=5 run on 2026-06-23"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["No CTO escalation: classified rule-execution per scanner-implementation-supervision-v1@v1.1 row 'Smoke test for an existing operation' plus local egress plumbing; no Scanner output shape, rubric, scoring, judge, corpus, or tamper-resistance reduction."]
---

# BEAAA-7796 outbound egress gate receipt

BLUF: implemented `post_bind_ingest.egress` as the single outbound monitor artifact gate for `tools/post-bind-ingest`; transmit remains local-only / no-op by default, and the adversarial "nothing raw escapes" test rejects raw PII, secret-like values, and transcript text before any transport call.

## Classification

- Deliverable: bug fix / smoke-test-backed egress gate for Scanner Engineer / CTO that causes post-bind monitor artifacts to pass through one local-only outbound boundary within the standard engineering allotment.
- Classification: rule-execution.
- Classification table row: `contracts/scanner-implementation-supervision-v1.md@v1.1` row "Smoke test for an existing operation" and local CLI/plumbing execution under existing monitor contract.
- Contract anchor: `contracts/are-scanner-api-v1.md@v1.0`; this change does not alter the BackBond Scanner API output shape.

## Implementation

- Added `tools/post-bind-ingest/src/post_bind_ingest/egress.py`.
- Exported the gate from `post_bind_ingest.__init__`.
- Updated `tools/post-bind-ingest/README.md`.
- Added `tools/post-bind-ingest/tests/test_egress_gate.py`.

Gate behavior:

- `build_monitor_artifact(...)` emits only:
  - aggregates,
  - `manifest://` pointers,
  - length-capped excerpts whose text must match the redacted-token pattern and carry `redaction_conformance: "validated"`.
- `transmit_monitor_artifact(...)` validates the artifact before transport.
- Default `EgressConfig()` and `EgressConfig.from_env()` are no-transmit unless `POST_BIND_INGEST_TRANSMIT=1`.
- Explicit transmit requires HTTPS plus an allowlisted endpoint host.

## Verification

Commands run from the Scanner workspace:

```text
PYTHONPATH=tools/post-bind-ingest/src pytest tools/post-bind-ingest/tests/test_egress_gate.py -q
```

Result: `6 passed`.

```text
PYTHONPATH=tools/post-bind-ingest/src pytest tools/post-bind-ingest/tests/test_receiver.py tools/post-bind-ingest/tests/test_cli.py tools/post-bind-ingest/tests/test_egress_gate.py -q
```

Result: `19 passed`.

```text
python3 - <<'PY'
from harness.golden_set.n5_band import run_l1_n5
r = run_l1_n5()
fixtures = r.get("fixtures", [])
breaches = [f for f in fixtures if f.get("are_breach") or f.get("subscore_breach")]
max_are_drift = max((f.get("are_spread", 0) for f in fixtures if f.get("baselined")), default=0)
print("baselined=", sum(1 for f in fixtures if f.get("baselined")), "/", len(fixtures))
print("max_are_drift=", max_are_drift)
print("breaches=", len(breaches))
print("pass=", len(breaches) == 0 and max_are_drift <= 1)
PY
```

Result:

```text
baselined= 8 / 9
max_are_drift= 0
breaches= 0
pass= True
```

## Operational metadata

| Field | Value |
|---|---|
| scanner_version | unchanged; no Scanner scoring path touched |
| corpus_version | unchanged; no corpus bytes touched |
| probe-set-version | unchanged; no probe pack touched |
| rubric_point_id | N/A — post-bind monitor egress boundary; no rubric point changed |
| N=5 calibration delta | L1 golden N=5 max ARE drift `0`, breaches `0`, pass `true` |
| upstream_eval_version | N/A |
| cto_cosign_ref | N/A — rule-execution |
| merged_sha_on_canon_master_at_close | pending canonical merge; do not close as merged until reachability is verified |

## FAL / self-audit

- Focus: pass. The artifact reinforces minimized post-bind monitoring data as underwriting evidence.
- Alignment: pass. It implements BEAAA-7796 acceptance without touching Scanner scoring, rubric, judges, corpus, or API output shape.
- Linkage: pass. Metadata cites BEAAA-7796 / BEAAA-7794 / BEAAA-7768 and contract versions.
- `agents/cmo-os.md`: unavailable in the local instruction tree during this heartbeat; FAL was run against available house-style and artifact metadata canon, and the missing file is disclosed rather than silently claimed.
