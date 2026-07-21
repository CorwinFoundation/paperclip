# BEAAA-16533 — AWS Bedrock/CloudWatch next-provider cross-provider increment

**BLUF:** Extended the QA-approved BEAAA-16490 / ADR-0039 cross-provider normalization
+ continuity harness with **AWS Bedrock/CloudWatch as a third, distinct provider stack**
(Provider C), ingested alongside each existing independent stack (OTel, Datadog) for one
synthetic deployed agent. Seven scenarios cover all five mandated cases; every scenario
emits **exactly one continuity band + one decision**, preserves AWS provenance, and
produces a hash-bound bundle that `verify_bundle` and `recompute_decision` both validate.
**N=5 Δ0** across full re-runs (decision + bundle hash identical). Prior BEAAA-16490 files
are **byte-stable**; no scoring path touched → **N=5 BackBond Score Δ0 by construction**.
Additive-only, fixture-only, **candidate-only, unpromoted**. Handed to QA (BEAAA-16531/16489
Track E).

---

## Canonical metadata

| Field | Value |
|---|---|
| `scanner_version` | v0.5.0 |
| `corpus_version` | 0.1.0 |
| `probe-set-version` | n/a — cross-provider continuity plane, no probe/eval added |
| `rubric_point_id` | n/a — no rubric point touched (evidence-plane plumbing only) |
| `candidate_sha` | (this commit — recorded in QA handoff) |
| `candidate_baseline` | 6301b889263b72236f4f185a0b2175316fd2d3a4 (BEAAA-16490) |
| `engine_version` | determination-engine@1.0.0 |
| `policy_version` | continuity-policy@1.0.0 (unchanged; reused) |
| `normalizer_version` | provider-normalizer@1.0.0 (unchanged; reused) |
| `contract` | are-scanner-api-v1@v1.0 — output envelope UNCHANGED |
| `N=5 calibration delta` | 0 (no scoring-path file changed; isolation guard passes) |
| `cto_cosign_ref` | not required for this increment (rule-execution, see classification) |
| `persona_audience` | internal (Brand Backbone gate not applicable) |
| `promotion` | candidate-only, unpromoted, behind emit flag; QA APPROVED required |

## Ticket classification (scanner-implementation-supervision-v1)

**Rule-execution.** This increment adds a synthetic provider fixture, a test, and a proof
script that *run under* the existing BEAAA-16490 rules. It reuses
`determination/provider_normalization.py`, `determination/continuity.py`, and
`continuity_policy_v1.yaml` **unchanged** — no rubric point, pillar weight, judge model,
scoring math, band cut, policy threshold, or contract version is altered. The N=5 BackBond
Score delta is 0 by construction because no scoring-path file changes (isolation guard +
git diff proof below). Per Supreme Law, N=5 still ran regardless of classification.

> The continuity policy **thresholds/semantics** remain flagged for CTO + Underwriter
> co-sign before governing any real (non-fixture) continuity/coverage decision — that gate
> is inherited from BEAAA-16490 and is **not** re-opened here; this increment adds no new
> policy content.

---

## Third provider profile (Provider C)

| Attribute | Value |
|---|---|
| `source_provider_id` | `provider-c-aws-bedrock` |
| `provider_stack` | `aws-bedrock-cloudwatch` |
| `attestation_type` | `cloudwatch_kms_signed_log_delivery` |
| `attestation_key_id` | `kid-C-kms-asym` (distinct signing key) |
| `authentication_method` | `aws_sigv4_kms_asymmetric_signature` |
| `independence_group` | `grp-aws-account-4417` |
| `manifest_signature_algorithm` | `ECDSA-P384-SHA384` |
| provider-native verdict | Bedrock Guardrails / CloudWatch anomaly — **provenance only**, never read by the resolver |

Synthetic data only. **No network credential and no real customer data** were used. PII,
prompts, weights, transcripts, and credentials are redacted/dropped by the reused normalizer
(`_DISALLOWED_KEYS` + PII masking) before retention.

---

## Per-scenario result table

| Scenario | Acceptance case | Sources (eligible) | Independence | Band | Decision |
|---|---|---|---|---|---|
| `aws_otel_corroboration` | (1) AWS+OTel corroborate candidate | AWS+OTel (2) | independent | **ELEVATED** | review_covered_peril_candidate |
| `aws_datadog_conflict_divergence` | (2) AWS vs Datadog conflict | AWS+Datadog (2) | independent | **DIVERGENCE** | divergence_hold |
| `aws_stale_degraded` | (3) AWS stale | OTel only (1) | fewer_than_two | **DEGRADED** | degraded_insufficient_independent_corroboration |
| `aws_unauthenticated_degraded` | (3) AWS integrity failed | Datadog only (1) | fewer_than_two | **DEGRADED** | degraded_insufficient_independent_corroboration |
| `aws_provider_verdict_cannot_override` | (4) AWS verdict no_incident/override | AWS+OTel (2) | independent | **ELEVATED** | review_covered_peril_candidate |
| `aws_shared_lineage_not_independent` | (5) AWS shares signing key with OTel | AWS+OTel (2) | **not** independent (shared_attestation_key) | **DEGRADED** | degraded_insufficient_independent_corroboration |
| `aws_otel_datadog_triple_corroboration` | bonus: 3-provider join | AWS+OTel+Datadog (3) | independent | **CONTINUE** | continue_monitoring |

Every row emits exactly one scalar band + one scalar decision. In
`aws_provider_verdict_cannot_override` the AWS Bedrock verdict `no_incident/override` is
retained in provenance yet the BackBond signal remains `covered_peril_candidate` and the
outcome stays ELEVATED — the provider verdict cannot flip it to CONTINUE.

## N=5 hash table (5 full re-runs of the complete scenario set, fixed inputs)

| Scenario | Band | Bundle SHA-256 | Distinct hashes across 5 runs | Δ0 |
|---|---|---|---|---|
| `aws_otel_corroboration` | ELEVATED | `0e1a2e6eea6129f0d01aabcbe45203eac3cbde187b254a82792f35f9de253ed0` | 1 | ✅ |
| `aws_datadog_conflict_divergence` | DIVERGENCE | `b181b610b3f6ec0362636525856c857c8d73c85ee80c21e6d206b4b876345ccd` | 1 | ✅ |
| `aws_stale_degraded` | DEGRADED | `c3ba4f7f055c6732f8d108598bb799cb3d8ca5b46376086cf73614c4ee55c747` | 1 | ✅ |
| `aws_unauthenticated_degraded` | DEGRADED | `4fbca858fdf223b71d24c3930f02c2ebbc143152417b2c2cdca33915079b8bc3` | 1 | ✅ |
| `aws_provider_verdict_cannot_override` | ELEVATED | `5de2c311e25b2b2e5c90fcd139a4dc30ebf2f35b0efeb949c10d55fbd1b73812` | 1 | ✅ |
| `aws_shared_lineage_not_independent` | DEGRADED | `8a42285c3df36c7757c650bcc3509c5edd2ce75b550a575c1ef02c6f95daf7be` | 1 | ✅ |
| `aws_otel_datadog_triple_corroboration` | CONTINUE | `f887e5ff4681d318823c11ecdfea335cbb9ccf89cfa3588a2d062943a66ff8b8` | 1 | ✅ |

`n5_delta_zero = True`. Serialized decisions and bundle hashes are identical across all
five runs.

---

## Changed-file isolation proof

New (additive) files only:

```
artifacts/beaaa-16533/aws_bedrock_cross_provider_evidence.json   (generated proof output)
artifacts/beaaa-16533/implementation-evidence-v0.1.md            (this doc)
determination/fixtures/cross_provider/synthetic_agent_aws_bedrock.json
scripts/beaaa16533_aws_bedrock_proof.py
tests/test_cross_provider_aws_bedrock_beaaa16533.py
```

`git status --porcelain` over the prior BEAAA-16490 surface returns **empty** (byte-stable):

```
determination/provider_normalization.py
determination/continuity.py
determination/rules/continuity_policy_v1.yaml
determination/fixtures/cross_provider/synthetic_agent_two_providers.json
scripts/beaaa16490_cross_provider_proof.py
tests/test_cross_provider_continuity_beaaa16490.py
artifacts/beaaa-16490/**
```

No scoring-path file (`rubric/scoring`, `rubric/bands`, corpus, API envelope) appears in the
diff → **N=5 BackBond Score Δ0 by construction**; the import-surface isolation guard
(`test_no_scoring_surface_imported`) enforces it.

## Exact commands & output

```
$ python3 -m pytest tests/test_cross_provider_aws_bedrock_beaaa16533.py -q
20 passed

$ python3 -m pytest tests/test_cross_provider_continuity_beaaa16490.py -q
20 passed

$ python3 -m pytest tests/ -q -k "determination or evidence or continuity or provider or cross_provider"
232 passed, 3 skipped, 2429 deselected, 14 subtests passed

$ python3 scripts/beaaa16533_aws_bedrock_proof.py
[OK] aws_otel_corroboration                   -> ELEVATED    (review_covered_peril_candidate)
[OK] aws_datadog_conflict_divergence          -> DIVERGENCE  (divergence_hold)
[OK] aws_stale_degraded                       -> DEGRADED    (degraded_insufficient_independent_corroboration)
[OK] aws_unauthenticated_degraded             -> DEGRADED    (degraded_insufficient_independent_corroboration)
[OK] aws_provider_verdict_cannot_override     -> ELEVATED    (review_covered_peril_candidate)
[OK] aws_shared_lineage_not_independent       -> DEGRADED    (degraded_insufficient_independent_corroboration)
[OK] aws_otel_datadog_triple_corroboration    -> CONTINUE    (continue_monitoring)
all_scenarios_passed = True
n5_delta_zero = True
```

The 3 skips are pre-existing and unrelated to this increment.

## Rollback

Revert only the five new BEAAA-16533 additions:

```
git rm determination/fixtures/cross_provider/synthetic_agent_aws_bedrock.json \
       scripts/beaaa16533_aws_bedrock_proof.py \
       tests/test_cross_provider_aws_bedrock_beaaa16533.py
rm -rf artifacts/beaaa-16533/
```

Existing ADR-0039 files and all prior BEAAA-16490 provider scenarios remain byte-stable.

## Promotion guard

**Candidate-only. Do not merge or promote.** Handed to QA Engineer
`cd1e82a0-d1cf-4b44-a8ea-a8b312120baa` via a child QA issue with the exact candidate SHA and
acceptance criteria. QA must record the exact word **APPROVED** on BEAAA-16533 for that SHA
before this increment counts. Public/production promotion remains behind the live-endpoint
gate (BEAAA-16355 Dev-Mode go-live / BEAAA-16380 GHCR) and Founder sign-off (BEAAA-15373 /
15400 governance) plus the inherited CTO + Underwriter policy co-sign.
