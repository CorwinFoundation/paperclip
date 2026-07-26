---
artifact_id: "BEAAA-17952-active-test-count-investigation"
artifact_type: "technical_investigation"
title: "Active Scanner published test count investigation"
status: "candidate"
version: "0.1"
created_at: "2026-07-26"
updated_at: "2026-07-26"
owner: "CTO"
persona_audience: "internal"
proof_points_cited:
  - "commit 1308ed7429d3fa7fe6c27f9dc9b9e653e94409ee"
  - "tools/active_test_registry.py"
  - "tools/active_test_registry_claims.json"
  - "tests/test_beaaa16647_aiuc_count_freeze.py"
  - "tests/test_active_test_registry_beaaa13644.py"
limitations:
  - "The current runner is single-turn; this count does not claim true multi-turn or tool-call coverage."
  - "Reported-not-scored families and target-bound rows lacking retained execution evidence do not count."
rollback: "Revert the BEAAA-17952 registry-claim commit; the prior published claim was 340, though the strict registry gate rejected it against the current 216-row executable set."
---

# Decision

Publish **216** Active Scanner tests: the exact number of distinct registry rows
that are active, production-runner executable, target-bound, and backed by the
required execution evidence. This changes no pillar weight, rubric point, score
math, or insurance mapping.

# Why the count was 1

Commit `1308ed7429d3fa7fe6c27f9dc9b9e653e94409ee` (BEAAA-13992) deliberately
reduced the published count from 90 to 1. The earlier registry treated
framework mappings as executable based on family membership. BEAAA-13992
introduced a fail-closed evidence gate requiring all of:

- concrete input or action;
- expected oracle;
- recorded target response;
- verdict; and
- GitHub provenance.

Only one row then satisfied the target-bound execution-evidence contract. The
value was therefore a data/evidence-gating result, not a display defect.

# Expansion and current reconciliation

Later work promoted concrete, target-bound cases across factuality,
consistency, refusal, calibration, injection/exfiltration, jailbreak, safety,
privacy, and consequential-action families. On the current candidate:

| Measure | Before BEAAA-13992 | BEAAA-13992 | BEAAA-17952 candidate |
|---|---:|---:|---:|
| Published active tests | 90 | 1 | 216 |
| Active executable rows | not evidence-gated | 1 | 216 |
| Target-bound runtime rows | not comparable | 1 | 290 |
| Distinct runtime cases | not comparable | 1 | 267 |

The prior frozen claim of 340 was stale: the strict registry computes 216 and
rejects 340 as exceeding the executable registry. BEAAA-17952 restores the
invariant `published_active_test_count == active_executable_count`.

# Verification

Run:

```text
PYTHONPATH=. python3 tools/active_test_registry.py --check
PYTHONPATH=. pytest -q tests/test_beaaa16647_aiuc_count_freeze.py tests/test_active_test_registry_beaaa13644.py
```

Expected: registry check exits 0; the focused count and evidence-gate tests
pass.

# Limitations

This batch makes existing single-turn, target-bound test evidence truthfully
publishable. It does not claim multi-turn/tool-call execution, and it does not
activate reported-not-scored families or assign score/insurance weights.
