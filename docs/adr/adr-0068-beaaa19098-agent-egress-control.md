# ADR-0068: isolate agent-egress-bench as customer-control evidence

BLUF: [BEAAA-19098](/BEAAA/issues/BEAAA-19098) adds a disabled-by-default,
local N=1 harness for customer egress controls; its results cannot enter agent
behavior registries, BackBond Scores, or `are-scanner-api-v1.md@v1.3.0` output.

## Decision

The CTO-corrected pinned 197-case, 18-category corpus (`e19efee`, tree
`d1d50c8d3e5a920311c53686f0d2fc3fdbe33d33`) executes only through a local
sealed, declarative `FixtureAdapter`. Arbitrary adapters and subclasses are
rejected before invocation, so executable callbacks cannot make model calls or
harmful side effects and then obtain a signed zero-call receipt. Applicability
is machine-readable and deterministic: a case is applicable only when its
transport is supported and every declared requirement is present.
`not_applicable` is emitted directly and is never a pass.

`control_type` is intentionally not an applicability input. The pinned corpus
declares transport and capability requirements, but no per-control-type
eligibility matrix; accepting that field would imply unsupported semantics.

Receipts use the `customer_controls.agent_egress` namespace and bind immutable
upstream commit/tree identifiers, per-case applicability and outcome, oracle
version, evidence URI/hash, partition counts, N=1 execution, and zero model-call
telemetry observed by the sealed boundary. They also bind
`execution_boundary=sealed-declarative-local-v1` and
`telemetry_verified=true`. An Ed25519 signature covers canonical JSON;
mutation fails verification.

## Isolation and invariants

No control case is registered with `tools.active_test_registry`; no scoring,
rubric, judge, target, simulator, or attacker path is imported. The harness
therefore preserves agent-behavior counts and produces no BackBond Score delta.
This is rule-execution under the “implement a probe within an existing rubric
point / customer harness” row of
`contracts/scanner-implementation-supervision-v1.md`.

## Rollback

Unset `BACKBOND_AGENT_EGRESS_CONTROL`. The default-off gate removes execution
and receipt emission without changing a score, contract response, or agent-case
count. Removing `harness/agent_egress_control.py` and the control-only vendored
corpus is a complete code rollback.

## Operational metadata

- `scanner_version`: `v1.0-compatible`
- `corpus_version`: `agent-egress-bench@e19efee4894994c887f6ae69bcdfbc05b93b56db`
- `probe-set-version`: `oss-005-control-v1`
- `rubric_point_id`: `control-only; not score-bearing`
- `N=5 calibration delta`: `0 by namespace isolation; report attached at handoff`
- `cto_cosign_ref`: `[BEAAA-19106](/BEAAA/issues/BEAAA-19106)` (source-pin correction)
- Contract: `contracts/are-scanner-api-v1.md@v1.3.0`
- FAL audit: pass; internal engineering artifact, no external claim
