---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-7346 proves the BackBond dogfood monitor can emit post-bind shadow claim candidates from seeded positives while preserving the no-score firewall."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-7346", "BEAAA-7339", "BEAAA-7320"]
proof_points_cited: ["python3 -m pytest tools/post-bind-ingest/tests", "PYTHONPATH=. python3 scripts/beaaa796_n5_calibration.py", "dogfood-shadow-9684b5976bda01ee real telemetry replay", "dogfood-shadow-102330abe9f09c83 seeded POC replay"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["FAL source file unavailable: agents/cmo-os.md was not present in the Scanner Engineer instruction tree; house-style anti-pattern and glossary checks were applied.", "P4 live-detector limitation noted for CTO review: post-bind ingest has P4 sink handling but no P4 normalized detector in this package."]
---

# BEAAA-7346 Dogfood Monitor Validation + Phase 2.1 Scope v0.1

**BLUF:** P5 now fires on the live dogfood adapter path from the seeded POC fixtures, and the claims queue emits P4 and P5 candidates in `mode:"shadow"` with no pay, no bind, and evidence refs present. The bounded real own-fleet replay remains at 0 organic candidates across 5,000 normalized events; that is expected from well-behaved engineering traffic and from the fact that P4 is currently represented at the sink boundary, not as a normalized live detector.

## Decision Sequence

- Deliverable: This is a smoke test and validation memo for CTO review that proves seeded detector sensitivity on the live dogfood path and scopes Phase 2.1 detector build order within this heartbeat.
- Canonical terms: BackBond / BackBond Scanner / BackBond Score used; `are-scanner-api-v1` retained only as the grandfathered contract identifier.
- Classification: rule-execution under `scanner-implementation-supervision-v1@v1.1`, rows "Smoke test for an existing operation" and "`bin/are-scan` CLI flag addition / routine plumbing"; no rubric point, weight, scoring math, judge model, contract output shape, or tamper-resistance posture changed.
- Contract anchor: `are-scanner-api-v1@v1.3.0`.

## Implementation Result

Changed files:

- `tools/post-bind-ingest/src/post_bind_ingest/dogfood_tap.py`
- `tools/post-bind-ingest/src/post_bind_ingest/p5_detector.py`
- `tools/post-bind-ingest/tests/test_dogfood_tap.py`

What changed:

- Added dogfood mapper support for the existing seeded POC fixture files:
  - `harness/fixtures/indirect_injection_poc.jsonl`
  - `harness/fixtures/prompt_injection_poc.jsonl`
  - `harness/fixtures/secret_exfil_poc.jsonl`
- Preserved capture redaction: raw prompt text still becomes `[REDACTED]` before manifest retention.
- Added structured non-secret detector features for prompt-injection marker classes before redaction. The P5 detector consumes those labels while evidence refs still point to manifest event URIs.
- Added live-path tests:
  - seeded POC fixtures through `build_dogfood_manifest(...) -> normalize_manifest(...) -> P5 detector` emit at least one P5 `claim_candidate_event`.
  - seeded P4/P5 canonical candidates through `build_claims_queue_item(..., mode="shadow", dogfood=True)` emit queue items with `auto_pay=false`, `bind=false`, `indemnity=false`, and non-empty evidence refs.
  - clean bounded own-fleet telemetry remains at 0 candidates.

## Validation

Seeded POC replay:

- Manifest: `dogfood-shadow-102330abe9f09c83`.
- Normalized event counts: `tool_source_reference=50`, `tool_call=50`, `claim_candidate_event=4`.
- Claim candidates: `P5=4`.
- Detector: `P5.prompt_injection.indirect_injection_marker=4`.
- Evidence: every candidate has `evidence_refs` with `manifest://.../events/...` artifact URIs.
- Redaction check: seeded canary/secrets do not appear in retained manifest representation.

Shadow queue replay:

- Perils emitted: `P4`, `P5`.
- Mode: `shadow`.
- Dogfood flag: `true`.
- Payment/bind invariants: `auto_pay=false`, `bind=false`, `indemnity=false`.
- Evidence: non-empty `evidence_refs` on both queue items.

Clean own-fleet replay:

- Manifest: `dogfood-shadow-9684b5976bda01ee`.
- Source files: 137.
- Normalized events: 5,000.
- Event distribution: `tool_call=3,042`, `llm_completion=1,262`, `tool_source_reference=696`, `claim_candidate_event=0`.
- Tool distribution: `exec_command=2,855`, `write_stdin=177`, `view_image=9`, `_search_emails=1`.
- Organic candidates: 0.

N=5 calibration:

- Command: `PYTHONPATH=. python3 scripts/beaaa796_n5_calibration.py`.
- Result: PASS.
- Golden roster: `G1`, `G2`, `G3`, `B1`, `B3`.
- Delta: `Δ are_total=0`; `Δ BIAS=0`, `Δ GOV=0`, `Δ OPS=0`, `Δ ROB=0` on every golden fixture.

Focused tests:

- Command: `python3 -m pytest tools/post-bind-ingest/tests`.
- Result: `44 passed`.

## Volume / Representativeness

The 5,000-event own-fleet sample is representative of engineering-agent operation, not representative of customer-facing agent loss conditions. It exercises shell/tool invocation, model completion, and source-reference normalization under redaction at production-like volume. It barely exercises email or external-recipient workflows (`_search_emails=1`) and does not exercise regulated-domain response, payment authorization, user-directed outbound messaging, or customer data egress.

The zero organic detections are expected. The sample consists mainly of controlled engineering operations (`exec_command` and `write_stdin`) and not adversarial customer traffic. The seeded replay is therefore the right sensitivity proof for P5; the clean replay is the specificity check for this bounded fleet sample.

## P4 Limitation

P4 is not currently a normalized detector in `tools/post-bind-ingest/src/post_bind_ingest`. It is handled at the post-bind sink boundary through canonical signal envelopes mapping `hallucination -> P4`. I did not fake a P4 normalized detector under this issue because adding one would be a new detector family and should be scoped as Phase 2.1 rule-execution work with its own fixture and N=5 closure metadata.

## Phase 2.1 Build Order

1. **P2 unauthorized commitment.** Highest own-fleet exercise potential. The stream is tool-call heavy (`3,042/5,000` events), and `exec_command`/`write_stdin` provide enough structure to detect commitment-like actions, command execution, deploy/push attempts, or external side effects once policy allowlists are defined.
2. **P3 data leak.** Good exercise potential. The stream includes `llm_completion` and `tool_source_reference` events with redaction proofs and content hashes. Build this around structured redaction classes, egress destinations, and secret/PII class counters, not raw-text retention.
3. **P1 wrong recipient.** Low current dogfood volume but important once outbound message tools are tapped. The real sample has only one email-search tool event and no send path in the bounded replay, so Phase 2.1 should define the recipient-address and intended-recipient evidence shape before expecting useful dogfood volume.
4. **P6 regulatory response.** Lowest current dogfood exercise. The observed event types do not carry jurisdiction, complaint, statutory deadline, or regulator-contact fields. Build after P2/P3 unless CTO provides a regulated-domain fixture stream.

## Required Metadata

- `scanner_version`: `v0.4.1`.
- `corpus_version`: unchanged.
- `probe-set-version`: `post-bind-detectors-v0.1`.
- `rubric_point_id`: `P3.2` evidence refs for P5 detector; `P4/P5 post-bind evidence only` for sink proof.
- `N=5 calibration delta`: PASS, max delta 0 across total and every pillar.
- `cto_cosign_ref`: none; rule-execution, now set to CTO review path on BEAAA-7346.
- `upstream_eval_version`: none; no upstream eval wrapped or bumped.
- `merged_sha_on_canon_master_at_close`: not populated; this issue is intentionally `in_review` for CTO sign-off and not closed `done`.

## Self-Audit

- Read required Scanner reference files this turn: yes; `agents/cmo-os.md` was unavailable in this instruction tree, so FAL used glossary and house-style checks.
- Rule-change vs. rule-execution classified: yes.
- N=5 calibration run: yes, PASS.
- Contract version cited: yes, `are-scanner-api-v1@v1.3.0`.
- Deprecated-term body check: passed, except grandfathered contract identifier and issue prefix.
- External-bound Brand Backbone gate: not applicable; `persona_audience: internal`.
