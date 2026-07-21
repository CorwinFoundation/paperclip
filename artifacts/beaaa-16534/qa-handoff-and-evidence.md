# BEAAA-16534 — AWS Bedrock/CloudWatch next-provider increment — QA handoff & evidence

**BLUF:** Added AWS Bedrock/CloudWatch as a third, distinct provider stack on top of the
QA-approved BEAAA-16490 ADR-0039 normalization harness. Additive fixture + tests + proof only —
the provider normalizer, continuity resolver, and `continuity-policy@1.0.0` are reused UNCHANGED.
5 acceptance scenarios pass, 21 new tests pass, BEAAA-16490 + determination/evidence-store
regression stays green (100 passed / 3 skipped), N=5 Delta 0 by construction. Candidate-only; do NOT promote.

**Contract anchor:** `are-scanner-api-v1@v1.0` (output semantics UNCHANGED).
**Classification:** rule-execution (reuses existing rule-content; no policy threshold, rubric, band-cut, or scoring change). Per `contracts/scanner-implementation-supervision-v1.md`, adding a fixture/adapter/test/proof under an existing, co-sign-pending policy is rule-execution. The underlying `continuity-policy@1.0.0` thresholds remain the rule-content already flagged for CTO+Underwriter co-sign in ADR-0039 — this increment does not move them.
**Promotion guard:** do NOT promote. QA must record the exact word `APPROVED` on BEAAA-16534 for the exact candidate SHA. Public/production promotion remains behind the live-endpoint gate + Founder sign-off (BEAAA-15400 / BEAAA-15373). Fixture-only, behind `BACKBOND_DETERMINATION_EMIT`.

## Scope / invariants honored

- Scanner version **v0.5.0**; corpus version **0.1.0**; built on candidate baseline `6301b889263b72236f4f185a0b2175316fd2d3a4`.
- Reuses the existing provider normalizer, continuity resolver, `continuity-policy@1.0.0`, and BEAAA-16490 proof shape. No parallel monitor; no policy threshold change.
- AWS synthetic envelope has a distinct provider id (`provider-c-aws`), stack (`aws-bedrock-cloudwatch`), signing key (`kid-C-sigv4`), authentication method (`aws_sigv4_cloudwatch_logs`), attestation type (`cloudwatch_signed_log_delivery`), independence group (`aws-acct-9910-us-east-1`), immutable event ids, timestamps, integrity status, and provider-native verdict retained as provenance only. No credentials or customer data.
- Provider-native verdict stays outside `DeterminationEngine.evaluate` and every deciding attribute. Conflict → DIVERGENCE; stale/missing/unauthenticated/shared-lineage → DEGRADED.
- Additive only: no BackBond Score math, rubric, corpus, band-cut, API-envelope, threshold, or existing expected-output change. No credentials/PII/prompts/weights/transcripts retained (existing redactor reused).

## New files (additive — nothing existing modified)

| File | Role |
|---|---|
| `determination/fixtures/cross_provider/synthetic_agent_aws_third_provider.json` | One synthetic agent, AWS third stack paired with each existing stack; 5 scenarios. |
| `tests/test_cross_provider_aws_increment_beaaa16534.py` | 21 tests: 5 acceptance cases + provenance + tamper/override + isolation guards + N-run determinism. |
| `scripts/beaaa16534_aws_cross_provider_proof.py` | Reuses the unchanged engine; emits the machine-verifiable AWS evidence bundle. |
| `artifacts/beaaa-16534/aws_cross_provider_continuity_evidence.json` | Emitted evidence bundle (generated). |
| `artifacts/beaaa-16534/qa-handoff-and-evidence.md` | This document. |

## Acceptance criteria → evidence

| Criterion | Scenario / test | Result |
|---|---|---|
| AWS + OTel corroboration → one band + decision | `aws_otel_corroboration_clean` / `test_aws_otel_corroboration_is_continue` | `CONTINUE` / `continue_monitoring` |
| AWS + Datadog conflict → DIVERGENCE (no averaging) | `aws_datadog_conflict_divergence` / `test_aws_datadog_conflict_diverges_without_averaging` | `DIVERGENCE` / `divergence_hold` |
| AWS stale/missing → DEGRADED | `aws_stale_degraded` / `test_aws_stale_drops_to_degraded` | `DEGRADED`; AWS ineligible, 1 eligible source |
| Contradictory AWS provider verdict cannot override BackBond | `aws_verdict_cannot_override` / `test_aws_provider_verdict_cannot_override_backbond` | `ELEVATED` despite AWS `no_incident`+`override:true` |
| Shared signing-key independence failure | `aws_shared_signing_key_not_independent` / `test_aws_shared_signing_key_fails_independence` | `DEGRADED`; reason `shared_attestation_key` |
| AWS ingested with EACH existing stack | `test_aws_ingested_with_each_existing_stack` | AWS × {OTel, Datadog} |
| Provenance preserved | `test_aws_provenance_preserved_through_normalization` | id/stack/SigV4 key/auth/independence-group/event-ids/integrity retained |
| Bundle validated by verify_bundle + recompute_decision | `test_bundle_is_machine_verifiable` (all 5) | band+decision+hash re-derive |
| Verdict not in deciding path | `test_recompute_ignores_tampered_aws_verdict` | band unchanged under tamper; hash flips (tamper detected) |
| No threshold/scoring/output change | `test_policy_thresholds_unchanged`, `test_no_scoring_surface_imported` | pins stable; no rubric/scoring import |

## N=5 determinism (fixed inputs, 5 runs — Delta 0)

Bundle `sha256` and full serialized decision are byte-identical across all 5 runs for every scenario:

| Scenario | Band | bundle_sha256[:16] | serialized_sha[:16] | Δ |
|---|---|---|---|---|
| aws_otel_corroboration_clean | CONTINUE | `1f6adbcc5e3e2101` | `5051d94c4449e8c2` | 0 |
| aws_datadog_conflict_divergence | DIVERGENCE | `7ef2a187f9793227` | `116b42fd6fb052dd` | 0 |
| aws_stale_degraded | DEGRADED | `d8a600e8c1e0e066` | `04c36956fc996bdc` | 0 |
| aws_verdict_cannot_override | ELEVATED | `747d7fbdd8e1b57f` | `f31f8ab1d956fc2d` | 0 |
| aws_shared_signing_key_not_independent | DEGRADED | `ab97013c935b9ec8` | `b1e31b2cf3822552` | 0 |

## N=5 BackBond Score calibration delta

**0 by construction.** No scoring-path file is touched — `git status` shows only new isolated
files; `git diff` on `determination/`, `scripts/`, `tests/`, `rubric/`, `artifacts/beaaa-16490/`
returns empty (prior ADR-0039 fixture + evidence are **byte-stable**). `test_no_scoring_surface_imported`
proves the reused modules import no `rubric.scoring`/`rubric.bands`/`rubric`. The scanner scoring
path is byte-identical, so `scripts/release_gate_n5.py` on this candidate SHA returns delta 0 on
every golden agent. Isolation guard enforced: **if any scoring-path file had changed or a score
moved, STOP for the CTO+Underwriter calibration gate.** It did not.

## Reproduce (from repo root)

```bash
# 1. New AWS increment suite (5 acceptance cases + provenance + tamper + isolation)
python3 -m pytest tests/test_cross_provider_aws_increment_beaaa16534.py -q      # expect 21 passed

# 2. No regression — BEAAA-16490 + determination + evidence-store
python3 -m pytest tests/test_cross_provider_continuity_beaaa16490.py tests/ \
    -q -k "determination or evidence_store or continuity or provider"          # expect 100 passed, 3 skipped

# 3. Production-path proof — emits the machine-verifiable AWS evidence bundle
python3 scripts/beaaa16534_aws_cross_provider_proof.py                         # expect all 5 scenarios OK

# 4. Independently machine-verify every emitted bundle
python3 - <<'PY'
import json
from determination.continuity import verify_bundle, recompute_decision
ev = json.load(open("artifacts/beaaa-16534/aws_cross_provider_continuity_evidence.json"))
assert all(verify_bundle(s["bundle"]) and recompute_decision(s["bundle"])["hash_matches"]
           for s in ev["scenarios"])
print("all AWS bundles machine-verified:", ev["all_scenarios_passed"])
PY
```

## Rollback

Revert only the new AWS additions: delete
`determination/fixtures/cross_provider/synthetic_agent_aws_third_provider.json`,
`tests/test_cross_provider_aws_increment_beaaa16534.py`,
`scripts/beaaa16534_aws_cross_provider_proof.py`, and `artifacts/beaaa-16534/`.
Prior ADR-0039 (BEAAA-16490) fixture, tests, proof, and evidence remain byte-stable.

## Metadata

- `scanner_version`: v0.5.0
- `corpus_version`: 0.1.0
- `probe-set-version`: unchanged
- `rubric_point_id`: N/A (runtime evidence-plane; no rubric point touched)
- `cto_cosign_ref`: N/A for this increment (rule-execution; underlying `continuity-policy@1.0.0` thresholds unchanged and already carry the ADR-0039 CTO+Underwriter co-sign flag)
- Baseline commit: `6301b889263b72236f4f185a0b2175316fd2d3a4`
- `persona_audience`: internal (Brand Backbone gate not applicable — no external-bound chrome)
