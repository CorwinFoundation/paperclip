---
artifact_type: adr
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-14455 preserves BackBond Scanner intake discipline without changing scoring, evidence, or the public contract shape."
contracts_referenced:
  - "are-scanner-api-v1.md@v1.0"
  - "scanner-implementation-supervision-v1.md@v1.1"
ticket_refs:
  - "BEAAA-14411"
  - "BEAAA-14455"
proof_points_cited:
  - "harness.egress._is_forbidden_resolved_ip"
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted:
  - "Classified as rule-execution / input-acceptance hardening per BEAAA-14411 CTO ruling; no contract bump and no Underwriter co-sign."
---

# ADR-0060: SSRF Boundary Hardening For Agent Endpoint Literals

BLUF: Reject loopback and link-local IP literals, including the cloud metadata address, at `agent_endpoint.host` intake while preserving RFC1918 private literals for the supported in-VPC deployment model.

## Context

BEAAA-14455 implements the F5 defense-in-depth follow-up from BEAAA-14411. Current validation treats `agent_endpoint.host` as a syntactic hostname and therefore accepts bare IPv4 literals such as `127.0.0.1` and `169.254.169.254`. The runtime egress layer already blocks forbidden resolved ranges through `harness.egress._is_forbidden_resolved_ip`; this ADR adds a boundary check for obvious literal-host inputs before a scan is accepted.

This is not a live vulnerability finding. The egress layer remains the load-bearing resolve-time SSRF control. The API boundary check is additive intake discipline.

## Decision

Add a reusable URL/host safety validator in `api.scan_handlers` that:

- Requires `https://` when validating a full URL value.
- Rejects userinfo in URL authority.
- Parses IP literals without DNS resolution.
- Reuses `harness.egress._is_forbidden_resolved_ip` for forbidden-range decisions.
- Lets callers preserve RFC1918 private literals when that is required by deployment shape.

Wire `agent_endpoint.host` through that helper with `allow_private_ip_literals=True`, so `127.0.0.1` and `169.254.169.254` return `422 agent_endpoint_host_forbidden`, while `10.0.3.14` remains accepted.

## Classification

Classification: rule-execution / input-acceptance hardening.

Contract row: `contracts/scanner-implementation-supervision-v1.md@v1.1`, "Smoke test for an existing operation" / internal behavior hardening under existing scanner rules; no rubric point, judge model, scoring math, band cut, pillar weight, output shape, or tamper-resistance reduction changes.

`contracts/are-scanner-api-v1.md@v1.0` remains unchanged. No contract bump. No Underwriter co-sign.

## VPC Caveat

Do not blanket-reject RFC1918 private literals for `agent_endpoint.host`. Some deployments run the BackBond Scanner container inside the vendor VPC and scan an internal service address. Rejecting `10.0.0.0/8`, `172.16.0.0/12`, or `192.168.0.0/16` at intake would change the supported deployment model and belongs in a CEO plus Underwriter decision path, not this F5 patch.

## Rejected Alternatives

- Reject all private ranges at API intake: rejected because it breaks in-VPC scanning.
- Reimplement the forbidden IP predicate in the API module: rejected because it creates duplicate security logic and drift risk.
- Resolve hostnames at the API boundary: rejected because DNS rebinding and redirect behavior must be checked at request time by the egress layer.
- Add webhook URL enforcement in this patch: rejected as out of scope for BEAAA-14455 acceptance criteria.

## Verification

- Reproduce-first commit: `2eca692` added tests that failed on `origin/master` because `127.0.0.1` and `169.254.169.254` were accepted.
- Focused tests after fix: `pytest tests/test_scan_handlers_ssrf_beaaa14455.py -q` -> 13 passed.
- Existing handler regression: `pytest tests/test_scan_handlers_beaaa547.py -q` -> 61 passed.

N=5 calibration delta: not run per BEAAA-14411 CTO issue ruling for this F5 input-acceptance hardening. Scoring math, corpus, probe set, judge models, output envelope, and BackBond Score calculation are untouched.

Operational metadata:

- `scanner_version`: unchanged
- `corpus_version`: unchanged
- `probe-set-version`: unchanged
- `rubric_point_id`: N/A, API input acceptance only
- `cto_cosign_ref`: BEAAA-14411 ruling; diff co-sign expected on PR per issue acceptance criteria

## Rollback

Revert the implementation commit that touches `api/scan_handlers.py` and the BEAAA-14455 test file. Runtime egress protection remains in place because this patch does not modify `harness.egress`.

## Self-Audit

Canonical terms check passed. House-style/FAL-equivalent check passed against available `canon/house-style.md`; no external-bound brand asset was created, so the Brand Backbone gate was not required.
