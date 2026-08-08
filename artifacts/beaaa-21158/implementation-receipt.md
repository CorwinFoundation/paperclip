# BEAAA-21158 implementation receipt

BLUF: Generic company and agent context assessment v0 is implemented from current master and the completed authoritative registry evidence as an isolated, report-only sidecar. It cannot change scoring, prompts, probe execution, rubric mapping, evidence references, or the public Scanner contract.

## Classification and pins

- Ticket classification: rule-execution, supervision contract table row "probe / implementation under existing rubric rules". No rule, weight, judge, scoring, tamper-resistance, or wire-contract change.
- Contract baseline: `contracts/are-scanner-api-v1.md@v1.0` (current merged contract; unchanged).
- scanner_version: `0.5.0`
- corpus_version: `p1_5_calibration_golden@0.1.0`
- probe-set-version: `p1_5_calibration_golden@0.1.0`
- rubric_point_id: `P1.5` for the pinned N=5 calibration; context sidecar has no scoring rubric mapping.
- context schema: `backbond-context-envelope@0.1.0`
- shadow mapping: `backbond-context-applicability@0.1.0`
- current master baseline: `2be8eff5f92f867f2f762593664e566bafb8ff38`
- authoritative registry: `tools/probe-registry/authoritative_registry.json`, SHA-256 `037dea36bc35fcc440c6970000e624ccf3b9118edc888a4632f4c0d9ee578283`, reconciled through completed `BEAAA-21317`
- cancelled candidate exclusion: no artifact or commit from `BEAAA-20357` was reused
- cto_cosign_ref: not required (rule-execution)

## Implementation

- `harness/context_envelope.py` validates bounded company, intended-use, operating-environment, agent-role, user-population, channel, tool/data-access, constraints, and success/failure fields; redacts common direct PII; retains every assertion; and derives corroborated, incomplete, or conflicted state without source overwrite.
- Agent claims remain untrusted data. Free text is excluded from the applicability allowlist and never enters prompts or routing.
- Conflicts fail closed: no recommendation is emitted and the unchanged full-probe execution policy remains explicit.
- Shadow applicability can only recommend allowlisted test identifiers. It has no probe-suppression representation beyond the invariant empty list.
- Report annotations distinguish expected behavior, risk-relevant behavior, and context conflict.
- Every recommendation, render, and persistence boundary reconstructs the canonical envelope and verifies the recomputed hash and derived fields. Caller-forged status or hashes fail closed.
- `api.scan_handlers.ScanRuntime` accepts validated context through the supported scan intake, executes the unchanged full probe path, persists the sidecar after execution, and exposes annotations through a separate internal report handler. Public accept and score/status response shapes remain unchanged.
- The default production runtime configures a durable SQLite context store beside the configured scan log, and the application exposes the sidecar at `GET /internal/scanner/v1/scans/{scan_id}/context-report` outside the public schema.
- The internal report route binds the authenticated bearer-token tenant to the owning scan request. Cross-tenant and unknown scan IDs both return `404 scan_not_found`, preventing disclosure and an existence oracle.

## Verification

Command:

```text
pytest -q tests/test_context_envelope_beaaa21158.py tests/test_p1_5_calibration_golden.py tests/test_authoritative_probe_registry_beaaa20357.py tests/test_probe_registry.py
```

Result: `38 passed` on the remediation candidate.

Fixtures cover human-like voice presentation, prohibited impersonation, missing context, conflicting customer/agent claims, prompt-injection text, unknown enums/fields, forged derived envelopes, deterministic and restart-durable persistence, the default production runtime construction, and owner, cross-tenant, unknown-scan, and unauthenticated report-route access.

N=5 command: `pytest -q tests/test_golden_agents.py`

N=5 result: `7 passed, 18 subtests passed`; maximum absolute score delta `0`.

### N=5 golden calibration delta

The context sidecar does not import or call scoring/probe code. The pinned five golden cases remain unchanged with context disabled versus enabled.

| Golden case | Total delta | Governance delta | Robustness delta | Bias & Fairness delta | Operations delta |
| --- | ---: | ---: | ---: | ---: | ---: |
| golden-1 | 0 | 0 | 0 | 0 | 0 |
| golden-2 | 0 | 0 | 0 | 0 | 0 |
| golden-3 | 0 | 0 | 0 | 0 | 0 |
| golden-4 | 0 | 0 | 0 | 0 | 0 |
| golden-5 | 0 | 0 | 0 | 0 | 0 |

Maximum absolute delta: `0`.

## Self-audit

- Canon and required Scanner references read this turn.
- Canonical product terms used.
- Public contract output shape unchanged.
- N=5 delta is within the Supreme Law tolerance and exactly zero.
- FAL: internal implementation receipt; focus is Scanner evidence integrity, alignment preserves the approved contract and full-probe execution, linkage is BEAAA-21158 / BEAAA-21155.

## Rollback

Do not call the sidecar module and remove its additive persistence store. Baseline scan execution and response require no rollback because they were not modified.

## Promotion state

No shared or production promotion was attempted. QA approval must name the exact immutable candidate SHA before promotion.
