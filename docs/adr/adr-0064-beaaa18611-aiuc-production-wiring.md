---
artifact_type: adr
title: "BEAAA-18611 AIUC production full-scan wiring"
owner: scanner_engineer
status: candidate
persona_audience: internal
created_at: "2026-07-28"
updated_at: "2026-07-28"
version: 0.1
prior_version: null
contracts_referenced:
  - "contracts/are-scanner-api-v1.md@v1.3.0"
  - "contracts/scanner-implementation-supervision-v1.md"
related_issues:
  - "BEAAA-18611"
  - "BEAAA-18614"
self_audit_passed: true
---

# BLUF

Wire the 20 human-escalation, 12 reference-tenant risk-pack, and 12
MCP-tool-integrity cases through the canonical registry and standard
customer-agent executor as reported-not-scored evidence.

## Classification

Rule execution under the supervision contract rows for implementing an
existing probe and production CLI/runtime plumbing. The change reuses the
shipped family cases, oracles, rubric identifiers, corpus pins, and graders. It
does not change scoring math, weights, bands, judge models, or the BackBond
Scanner API response shape.

## Decision

`tools.active_test_registry` materializes cases directly from the existing
family specs and gives each family/case pair one canonical runtime identity.
`api/scan_runtime_factory.py` and `api/full_scan_executor.py` already consume
this registry and the registered-case worker, so no parallel selection surface
is introduced.

The standard customer-agent runner asks for the family’s existing structured
boolean decision and passes the parsed response to the existing deterministic
grader. The runner records the rendered surface, response, oracle, verdict,
surface seed, model pin, evidence URI, and budget counters.

MCP tool-integrity cases execute only when trusted target capabilities declare
MCP or A2A. An absent declaration emits terminal `not_applicable` evidence
without calling the target; it can never become a pass.

## Consequences

- Production target-bound evidence rows increase by exactly 44, from 290 to
  334.
- The published scored/executable count remains 216 because all three families
  remain reported-not-scored.
- Every new runtime identity is unique and canonical; no alias row executes.
- Rollback is the isolated BEAAA-18611 wiring commit.

## Verification

- Focused registry, evaluator, executor, applicability, and full-scan
  regression gate: 136 passed.
- Golden roster N=5, seeds 0 through 4: total and every pillar delta 0 for B1,
  G1, G2, M1, M2, and M3.
- Canonical QA child: BEAAA-18614 must record `APPROVED` on the exact final
  candidate SHA before any promotion.

## FAL audit

Internal engineering ADR. Focus: production Scanner evidence. Alignment:
reported-not-scored status and existing contract are preserved. Linkage:
BackBond Scanner API contract and canonical QA gate are explicit.
