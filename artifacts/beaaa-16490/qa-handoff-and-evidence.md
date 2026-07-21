# BEAAA-16490 — QA handoff & evidence

**Contract anchor:** `are-scanner-api-v1@v1.0` (output semantics UNCHANGED).
**Classification:** rule-execution (code) + rule-content flagged for CTO+Underwriter co-sign (policy thresholds). See ADR-0039.
**Promotion guard:** do NOT promote. QA `APPROVED` on the exact candidate SHA + Founder sign-off required (BEAAA-15400 / BEAAA-15373). This candidate is fixture-only and behind `BACKBOND_DETERMINATION_EMIT`.

## What changed (six isolated new files — nothing existing modified)

| File | Role |
|---|---|
| `determination/provider_normalization.py` | Provider-native telemetry input class; provenance-preserving normalizer; PII/disallowed-key redaction; independence check. |
| `determination/continuity.py` | Cross-provider continuity decision engine + machine-verifiable bundle (`verify_bundle` / `recompute_decision`). |
| `determination/rules/continuity_policy_v1.yaml` | Versioned deterministic continuity policy `continuity-policy@1.0.0` (rule-content; co-sign pending). |
| `determination/fixtures/cross_provider/synthetic_agent_two_providers.json` | One synthetic agent, two independent provider stacks, 6 scenarios. |
| `tests/test_cross_provider_continuity_beaaa16490.py` | 20 tests: 3 deterministic cases + provenance + negative override + rollback/isolation guards. |
| `scripts/beaaa16490_cross_provider_proof.py` | Production-path proof; emits the reachable evidence bundle. |

## Acceptance criteria → evidence

- **Two independent provider stacks → ONE band + ONE decision:** `test_scenario_resolves_to_single_band_and_decision`, all scenarios.
- **Three deterministic cases:** corroboration (`CONTINUE`/`ELEVATED`), conflict/divergence (`DIVERGENCE`), missing/stale (`DEGRADED`) — `test_three_deterministic_cases_present`, `test_divergence_does_not_average`.
- **Provenance preserved:** `test_provenance_preserved_through_normalization` (provider identity, immutable source event ids, attestation type, timestamps, integrity/auth status, independence result).
- **Provider verdict cannot override BackBond:** `test_provider_verdict_cannot_override_backbond`, `test_recompute_ignores_tampered_provider_verdict`.
- **Machine-verifiable + reachable bundle:** `test_bundle_is_machine_verifiable_and_reachable`; artifact `artifacts/beaaa-16490/cross_provider_continuity_evidence.json`.
- **PII redaction / no customer weights or prompts retained:** `test_pii_and_disallowed_keys_redacted_before_retention`.
- **Fail-closed:** `test_unauthenticated_source_is_ineligible`, `test_unknown_integrity_status_fails_closed`, `test_cross_agent_evidence_rejected`, independence `test_independence_check_flags_shared_key`.
- **No score movement / rollback isolation:** `test_no_scoring_surface_imported`, `test_policy_pins_are_stable`.

## How to reproduce (from repo root)

```bash
# 1. New cross-provider suite (acceptance + negative + rollback guards)
python3 -m pytest tests/test_cross_provider_continuity_beaaa16490.py -q      # expect 20 passed

# 2. No regression in the underlying determination engine
python3 -m pytest tests/test_determination_engine_beaaa11372.py \
                  tests/test_evidence_store_read_adapter_beaaa11378.py -q     # expect 55 passed, 1 skipped

# 3. Production-path proof — emits the machine-verifiable evidence bundle
python3 scripts/beaaa16490_cross_provider_proof.py                           # expect all 6 scenarios OK

# 4. Independently machine-verify the emitted bundle
python3 - <<'PY'
import json
from determination.continuity import verify_bundle
ev = json.load(open("artifacts/beaaa-16490/cross_provider_continuity_evidence.json"))
assert all(verify_bundle(s["bundle"]) for s in ev["scenarios"])
print("all bundles machine-verified:", ev["all_scenarios_passed"])
PY
```

## N=5 calibration delta

**0 by construction.** No scoring-path file is touched (git status shows six new isolated files; `test_no_scoring_surface_imported` proves the modules import no `rubric.scoring`/`rubric.bands`/`rubric`). The scanner scoring path is byte-identical, so `scripts/release_gate_n5.py` on this candidate SHA would return delta 0 on every golden agent. QA may run it for independent confirmation.

## Metadata

- `scanner_version`: v0.5.0
- `corpus_version`: 0.1.0
- `probe-set-version`: unchanged
- `rubric_point_id`: N/A (runtime evidence-plane; no rubric point touched)
- `cto_cosign_ref`: PENDING (continuity-policy content; code plumbing is rule-execution)
- Baseline commit: `a28e9bc0d2e907005c59642b999ad7d8ce11de30`
