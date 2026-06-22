---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "The BEAAA-7339 dogfood run proves BackBond can monitor real own-fleet traces in shadow mode before customer live-bind gates open."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-7339", "BEAAA-7320"]
proof_points_cited: ["python3 -m pytest tools/post-bind-ingest/tests", "PYTHONPATH=. python3 scripts/beaaa796_n5_calibration.py"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["FAL source file unavailable: agents/cmo-os.md was not present in the Scanner Engineer instruction tree; house-style anti-pattern and glossary checks were applied."]
---

# BEAAA-7339 Dogfood Shadow-Monitor Run Report v0.1

**BLUF:** BEAAA-7339 is implemented on branch `beaaa-5487-ci-pytest` as rule execution. The dogfood tap normalized real own-fleet traces into the existing post-bind ingest pipeline with capture redaction, no scoring/pricing imports, and shadow/no-pay claims queue invariants.

## Scope

- Contract anchor: `are-scanner-api-v1@v1.3.0`.
- Supervision contract: `scanner-implementation-supervision-v1@v1.1`.
- Scanner metadata: `scanner_version: v0.4.1`; `corpus_version: unchanged`; `probe-set-version: post-bind-detectors-v0.1`; `rubric_point_id: P4/P5 post-bind evidence only`; `cto_cosign_ref: none, rule-execution`.
- Deliverable: additive dogfood adapter + shadow-mode invariant tests + ADR-0036 for CTO review.

## Pilot Run

- Window: Codex trace files from `2026-06-21` through `2026-06-22`; Claude bounded engineering transcript sample from `.claude/projects`.
- Source files read: 195.
- Manifest: `dogfood-shadow-5325cb2c7c9a6f60`; signature algorithm `sha256`.
- Manifest events emitted: 5,000.
- Normalized events retained: 5,000.
- Runtime: 3.0s wall-clock for bounded sample generation and normalization.

## Event Counts

| event_type | count |
| --- | ---: |
| `tool_call` | 3,042 |
| `llm_completion` | 1,262 |
| `tool_source_reference` | 696 |
| `claim_candidate_event` | 0 |

## Detector Results

- P5 detector candidates: 0 in the bounded real sample.
- P4 claims queue candidates: 0 in the bounded real sample.
- Evidence refs: not emitted because there were no claim candidates.
- False-positive review: no positives to adjudicate.

## Redaction

- Redaction records attached: 5,000.
- Retained raw transcripts: false.
- Capture policy redacted prompt/completion/tool input/tool result fields before manifest retention.
- Raw email/token smoke check passed in tests; fixture secrets do not survive manifest/event representation.

## Real-Traffic Coverage Notes

- P4/P5: exercised now through tool/source/completion event mapping and P5 detector pass over normalized events.
- P1/P2/P3/P6: real traffic would exercise these once detector families exist for wrong-recipient action, unauthorized commitment, data leak, and regulatory-response signals. This is Phase 2.1 scoping input only; BEAAA-7339 does not implement those detectors.

## Verification

- `python3 -m pytest tools/post-bind-ingest/tests` → 42 passed.
- `PYTHONPATH=. python3 scripts/beaaa796_n5_calibration.py` → PASS; G1/G2/G3/B1/B3 all Δ total 0 and Δ pillar 0.
- No-score firewall: `test_dogfood_tap_does_not_import_scoring_or_pricing_modules` and existing sink firewall passed.
- Shadow invariant: `test_claims_queue_shadow_mode_is_no_pay_no_bind_for_dogfood` passed.
