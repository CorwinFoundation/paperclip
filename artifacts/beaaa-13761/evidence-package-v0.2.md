# Execution evidence package v0.2

This package supersedes v0.1 for executable-count and production-dispatch claims.

## Truthful count

- Catalog rows: 280
- Active production-executable cases: 90
- Quarantined rows: 190
- Historic 1,180 claim: unsupported
- Standard full-scan target calls: 110
- Standard full-scan judge calls: 90

Active families:

- `p1_1_factuality_golden`: 30 cases
- `p1_3_consistency_golden`: 20 cases
- `p1_4_refusal_golden`: 20 cases
- `p1_5_calibration_golden`: 20 cases

## What changed from v0.1

Version 0.1 proved fail-closed dispatcher plumbing with unavailable workers. It did not execute the target and therefore did not establish 281 executable cases. Version 0.2 corrects that claim: only rows with bound target payload and result adapters are active; the production full tier bypasses the legacy four-prompt walker; every attempt writes atomic evidence; and missing work is incomplete rather than silently counted.

The initial judge authorization is capped at $100 before calls are made. Analysis-only completion does not create a placeholder insurance score.

## Generated proofs

- `artifacts/beaaa-13644/active_test_registry.json`
- `artifacts/beaaa-13644/active_test_counts.json`
- `tools/probe-registry/active_registry.json`
- `artifacts/beaaa-13761/registry/current-canonical-registry.json`
- `artifacts/beaaa-13761/registry/framework-worker-manifest.json`

All five generated views reconcile to 90 executable cases.

## Verification

Focused execution, registry, dispatcher, canonical-registry, and worker-manifest tests pass. The broad Windows run reported 2,278 passes; its remaining failures reproduce on the untouched branch and are dominated by Linux-only multiprocessing, byte-locked corpus files checked out with Windows line endings, and pre-existing response-envelope tests.

The controlled demo acceptance artifact is written after deployment from the signed live manifest. It must show:

```text
expected = attempted = terminal = evidence_rows = 90
target_calls = 110
judge_calls = 90
estimated_judge_cost_usd <= 100
```
