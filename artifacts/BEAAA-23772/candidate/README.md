---
artifact_type: implementation_closeout
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This candidate preserves a Founder-gated one-shot deployment authorization when configured judge infrastructure is unavailable."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-23772", "BEAAA-23763", "BEAAA-23759"]
proof_points_cited: ["BEAAA-23763 exact-candidate QA finding ee982874"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["Independent QA approval is required before installation; this issue does not authorize installation or deployment."]
---

# BEAAA-23772 immutable readiness-before-consumption candidate

BLUF: when the configured proxy judge socket reports `missing`, the helper now returns non-zero before the atomic authorization rename. The allow file therefore survives an infrastructure outage. The `ready` path still performs exactly one rename, and every existing privilege and output boundary remains unchanged.

## Scope and classification

- Deliverable: immutable internal candidate bytes, complete SHA-256 manifest, symmetric hash-gated install and rollback scripts, and focused tests for independent QA.
- Classification: rule-execution under the supervision table row `Smoke test for an existing operation`, paired with the existing helper's internal control-flow correction. No rubric, judge model, scoring math, output JSON, corpus, sudoers, SSH, group, ACL, service, marker, grant, or deployment state changes.
- Scanner contract: `contracts/are-scanner-api-v1.md@v1.3.0`; Scanner output shape is unchanged.
- Calibration: N=5 delta is `0 / N/A (non-scoring deployment helper control flow)`. This candidate cannot enter the scoring path.

## Exact behavior

The candidate differs from the QA-approved V2 helper by two executable lines:

```python
if socket_status == "missing":
    return 1
```

Those lines run after the names-only `JUDGE_SOCKET: missing` status is printed and before `consume_authorization()` is called. A configured socket that is ready retains the existing single atomic rename. A non-proxy judge configuration retains its existing consumption path.

## Install and rollback boundary

Installation is prohibited on BEAAA-23772. After independent QA approves the exact candidate commit and hashes, the authorized installation issue may run:

```sh
sudo ./install-helper.sh "$(pwd)"
```

The installer refuses unless the live helper is exactly the approved V2 baseline hash, `root:root`, mode `0755`. It stages and re-verifies the replacement before a same-directory atomic move.

Rollback is symmetric:

```sh
sudo ./rollback-helper.sh "$(pwd)"
```

It refuses unless the live helper is exactly this candidate hash and the shipped rollback file is exactly the V2 baseline hash. Neither script changes or consumes deployment authorization state.

## QA acceptance

QA must verify every manifest hash, confirm the candidate-versus-baseline diff is limited to the two-line pre-consumption gate, execute the focused tests, and test install/rollback against isolated root-owned paths or a host-equivalent sandbox. QA must record `APPROVED` for the exact candidate commit on the one canonical QA child before any installation issue proceeds.

## Operational metadata

- scanner_version: unchanged; deployment helper only
- corpus_version: unchanged / not touched
- probe-set-version: unchanged / not touched
- rubric_point_id: `N/A` (deployment authorization control flow)
- N=5 calibration delta: `0 / N/A (non-scoring path)`
- cto_cosign_ref: `N/A` for rule-execution; QA gate remains mandatory before installation
- FAL audit: pass; internal engineering artifact, one factual outcome, exact hashes and ticket provenance
