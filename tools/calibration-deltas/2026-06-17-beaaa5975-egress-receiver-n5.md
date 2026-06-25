---
artifact_type: other
owner: cto
persona_audience: internal
phase: 2
canonical_terms_only: true
focus_claim: "This receipt preserves BackBond Scanner reproducibility while adding the Phase 2.5 signed post-bind evidence receiver."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1", "post-bind-egress-wire-v1@v1.0"]
ticket_refs: ["BEAAA-5975", "BEAAA-5966"]
proof_points_cited: ["ADR-0035 §6.2", "tools/post-bind-ingest/tests/test_receiver.py", "tests/test_beaaa398_wiring.py"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["Rule-execution per ADR-0035 §8(d) and §8(i); no scanner output shape, scoring math, band cut, or judge-model change."]
---

# BEAAA-5975 Egress Receiver N=5 Receipt

**BLUF:** PASS. Phase 2.5 receiver verification is deterministic and rejects replay and adversarial mutations. BackBond Scanner L1 N=5 wiring remains in band with no scoring-path changes.

## Classification

- Deliverable: receiver implementation plus wire contract for CTO and Head of Compliance that replaces the deprecated S3 manifest reader boundary with mTLS plus ed25519 JWT batch verification.
- Supervision row: ADR-0035 §8(d) "Egress receiver retrofit" and §8(i) "Schema-v1.0 wire-payload contract publish"; both classified rule-execution.
- Contract anchor: `are-scanner-api-v1@v1.0`; this change does not alter `are_total`, subscores, band, confidence, evidence requirements, scoring math, judge models, or scanner output shape.

## Versions

| Field | Value |
|---|---|
| scanner_version | unchanged by this action; BackBond Scanner API remains `are-scanner-api-v1@v1.0` |
| corpus_version | unchanged by this action; receiver schema pin is `log-event-schema-v1.0` |
| probe-set-version | unchanged by this action; no probe or judge added |
| rubric_point_id | `OPS.EVIDENCE_PIPELINE.POST_BIND_EGRESS` implementation surface; no rubric point added or reweighted |
| N=5 calibration delta | BackBond Scanner L1 wiring gate passed; receiver schema conformance N=5 passed; receiver replay-defense N=5 passed |
| cto_cosign_ref | BEAAA-5966 ADR-0035 co-sign complete per CTO handoff |

## Verification

Command:

```bash
python3 -m pytest tools/post-bind-ingest/tests -q
```

Result:

```text
35 passed in 1.08s
```

Coverage in that run:

- ed25519 JWT envelope verification accepts valid batches.
- Invalid JWT signature is rejected.
- Replay `batch_id` is rejected.
- Wrong `schema_version` is rejected.
- Wrong `batch_sha256` is rejected.
- TLS server context pins TLS 1.3 only and requires client certificates.
- Existing normalizer, detector, and sink semantics remain covered by the post-bind ingest tests.
- Receiver schema-conformance N=5 loop passed.
- Receiver replay-defense N=5 loop passed.

Command:

```bash
python3 -m pytest tests/test_beaaa398_wiring.py -q
```

Result:

```text
7 passed in 1.02s
```

Interpretation: the repo-level L1 N=5 golden wiring gate remains green. Because BEAAA-5975 adds an isolated post-bind receiver package and does not import into the scanner scoring path, expected score delta is 0 and no rule-change escalation is triggered.

## Adversarial Mutation Receipt

| Mutation | Expected result | Receipt |
|---|---|---|
| Invalid JWT signature | reject | `test_parse_receiver_payload_rejects_bad_signature` |
| Replay `batch_id` | reject | `test_normalize_receiver_events_replay_batch_id_rejected`; N=5 loop `test_receiver_replay_defense_n5_rejects_replays` |
| Wrong schema version | reject | `test_parse_receiver_payload_rejects_wrong_schema_version` |
| Wrong batch SHA-256 | reject | `test_parse_receiver_payload_rejects_wrong_batch_sha` |

## FAL Audit

- Focus: passes. The artifact supports BackBond's rate-and-insure loop by preserving signed, typed, post-bind evidence.
- Alignment: passes. No evaluation-as-product, governance-platform, or outcome promise language introduced.
- Linkage: passes. References BackBond Scanner, signed evidence, post-bind logs, ADR-0035, and the scanner contract chain.
- Carrier Rule: passes. No external carrier is named.
