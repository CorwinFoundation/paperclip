---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "v1.5.0 contract patch (DRAFT pending UW op-seat co-sign and subprocess-end-to-end calibration; OQ-1/OQ-2 CTO-CONFIRMED on comment 77974a04) — internal Scorer-to-API IPC surface change introducing the score-envelope return channel, the closed ALLOWED_SCORE_ENVELOPE_FIELDS allow-list, and the ('ok', envelope) / ('err', type, msg) tuple sentinel mirroring the Prober result_queue contract. No customer-visible binding shape change. Envelope dict schema (ADR-0017 addendum-1 §2.2) is byte-identical to the v1.2.x lineage; the value source moves from the deleted api.score_runtime API-edge module into the Scorer subprocess (scanners.score_runtime) per BEAAA-2182 Track A. Tamper resistance strengthened (scoring now runs inside ScopedEgressAllowlist([]) deny-all scope)."
contracts_referenced: ["are-scanner-api-v1@v1.4.0 → v1.5.0", "scanner-implementation-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-2182", "BEAAA-2175", "BEAAA-2132", "BEAAA-2823"]
proof_points_cited: ["BEAAA-2182 CTO design co-sign comment 9840343c (Track A ACCEPTED 2026-06-05) — Q-A1 pre-load Rubric / Q-A2 tuple sentinel / Q-A3 two-layer AG-A5 / ADR sign-off table with v1.2.1 → v1.3.0 contract bump nomination", "ADR-0026 (this ticket — scanner engineer authored 2026-06-05)", "AG-A1..AG-A6 fixtures at tests/test_beaaa2182_a_scorer_wiring.py (26 tests, all pass)", "AG-A2 N=5 spread = 0 at tools/calibration-deltas/2026-06-05-beaaa2182-track-a-n5.md (in-process Scorer-side projection determinism)", "AG-A5 CI gate at scripts/check_no_apiedge_score.py (api.score_runtime re-introduction blocked; legacy /score rubric.scoring import surfaced informationally only)"]
version: "v1.5.0-patch-draft"
prior_version: "are-scanner-api-v1@v1.4.0"
self_audit_passed: true
self_audit_note: "DRAFT pending CTO confirmation on OQ-1 (target version selection — see escalations_noted) AND UW op-seat co-sign per v1.3.0 §7 'rule changes that affect the contract output shape' row (CTO co-sign comment 9840343c flagged uw_cosign_ref as pre-merge pending). Pre-submission self-audit: (1) Focus claim cites the binding CTO co-sign comment, the AG-A1..AG-A6 fixtures, and the AG-A2 calibration delta. (2) Carrier Rule clean — no carrier name in body. (3) Canonical terms only (ARE / IFA; no BMI / ARI / BEAI). (4) Internal-only artifact (Scorer-to-API IPC is not customer-visible; binding envelope dict shape is unchanged from v1.2.x). (5) Supreme Law: AG-A2 spread = 0 satisfied in-process; subprocess-end-to-end golden calibration is the CTO merge gate per the next-steps row in CTO co-sign comment 9840343c. (6) Tamper resistance: scoring now runs inside ScopedEgressAllowlist([]) deny-all egress scope (strictly stronger than the BEAAA-2175 PR-B API-edge scoring posture this Track A supersedes). (7) Rollback: ARE_SCORER_REAL=off Tier-1 ≤10 min reverts to the master 52c003e _phase1_pending placeholder envelope. (8) Versioning OQ flagged at escalations_noted item 1."
escalations_noted:
  - "OQ-1 — target version selection — RESOLVED 2026-06-05 by CTO comment 77974a04. Confirmed v1.5.0 (registry check: v1.3.0 → BEAAA-2023 telemetry-shim done; v1.4.0 → BEAAA-2474 agentic-pilot-readiness done with UW co-sign at BEAAA-2491). Track A is a distinct IPC-contract delta from v1.4.0's output-surface delta and gets its own patch entry for forward auditability — NOT an addendum to v1.4.0."
  - "OQ-2 — UW op-seat co-sign classification — RESOLVED 2026-06-05 by CTO comment 77974a04. Confirmed §7 'contract additive minor' classification — UW operational co-sign sufficient (process-model topology change); HoUW binding co-sign NOT required (no persistence-semantics / verdict-semantics / enum-widening / selection_rule / probe-set / judge / rubric / weights touch; §2.2 envelope dict byte-identical; tamper resistance strengthened). Per reference_uw_supervision_boundary: operational = UW, binding = HoUW; Track A is purely operational."
  - "HoUW countersign NOT triggered per OQ-2 CTO ruling. Founder gate NOT triggered (no pillar weight change, no rubric weight change, no canonical-term change, no covered-peril definition change; tamper resistance STRENGTHENED by moving scoring into ScopedEgressAllowlist([]) deny-all scope — strictly stronger posture)."
---

# Contract patch — `are-scanner-api-v1.md` v1.4.0 → v1.5.0

**Status:** DRAFT pending (a) UW op-seat co-sign per v1.3.0 §7 "contract additive minor" Approval-gates row and (b) subprocess-end-to-end N=5 calibration spread = 0 vs B1/G1/G2/M1/M2/M3 via `bin/are-scan` (CTO merge-co-sign gate per comment `77974a04`). OQ-1 (target version) and OQ-2 (UW classification, HoUW NOT required) CTO-CONFIRMED 2026-06-05 in comment [`77974a04-1f50-4eea-a4cb-8a89303c91a3`](/BEAAA/issues/BEAAA-2182#comment-77974a04-1f50-4eea-a4cb-8a89303c91a3).
**Change classification:** internal IPC surface change. The Scorer-to-API return channel is added; the binding envelope dict shape (§2.2 fields, JCS-canonicalized snapshot_hash domain) is UNCHANGED from v1.2.x. No customer-visible field added, renamed, or removed. The value-source for the §2.2 fields moves from the deleted `api/score_runtime.py` API-edge module into the Scorer subprocess (`scanners/score_runtime.py`) per BEAAA-2182 Track A ADR-0026.
**Rollback:** Tier 1 — `ARE_SCORER_REAL=off` env flip ≤10 min. Reverts to the master `52c003e` `_phase1_pending` placeholder envelope. Tier 2 PR revert ≤2 h. Tier 3 parent flag `ARE_RESCANS_MEASURED=off` per ADR-0017 §6.

---

## §0. Scope

This patch documents the internal Scorer-to-API IPC surface change that lands with BEAAA-2182 Track A. The binding envelope shape (the dict the §Hard-guarantee #2 evidence-trail completeness rests on) is byte-identical to v1.2.x. The change is structural — where the §2.2 envelope is computed (API edge → Scorer subprocess) — not material — what the §2.2 envelope says.

## §1. New surface (internal) — Scorer→API score envelope return channel

A second `multiprocessing.Queue` is added to `WorkerChannel` (one slot per scan). The Scorer subprocess, after END_OF_SCAN, projects the §2.2 envelope via `scanners.score_runtime.ScoreRuntime.compute_envelope` (the same `rubric.scoring.score` callee the CLI offline path uses) and ships the result back to the API-edge parent over this return queue, wrapped in a tuple sentinel:

```python
# Scorer side
channel.send_score_envelope(("ok", envelope_dict))
# OR
channel.send_score_envelope(("err", type_name, message))

# Parent side
outcome = channel.recv_score_envelope(timeout=...)
if outcome[0] == "ok":
    score_envelope_registry[scan_id] = validate_inbound_envelope(outcome[1])
else:
    raise OrchestratorError(f"scorer subprocess raised {outcome[1]}: {outcome[2]}")
```

Mirrors the existing Prober `result_queue` contract at `api/scan_runtime_factory.py::_prober_target` — same tuple shape, same error-surface idiom (Q-A2 ACCEPT on CTO co-sign comment `9840343c`).

## §2. New closed allow-list — `ALLOWED_SCORE_ENVELOPE_FIELDS`

The return-channel dict is validated against a closed allow-list on both serialize and receive sides (defense in depth — Hard Invariant #4 is not contingent on a well-behaved Scorer):

```python
ALLOWED_SCORE_ENVELOPE_FIELDS: frozenset[str] = frozenset({
    "agent_id", "are_total", "band",
    "confidence_below_adjudication_threshold", "corpus_version",
    "evidence_refs", "model_versions", "partial_completion",
    "prior_scan_refs", "prior_scan_refs_truncated",
    "purpose", "rubric_version", "run_timestamp",
    "scanner_version", "seed", "subscores",
})
```

The set = ADR-0017 addendum-1 §2.2 envelope fields + the `prior_scan_refs_truncated` cap sentinel per ADR-0017 §7 Q2. Mirrors the `ALLOWED_ROW_FIELDS` shape for the inbound Prober→Scorer row queue. Every leaf is JSON-primitive per `_is_pure_json_value`; no opaque bytes; no `CredentialVault` / `CredentialRef` instances.

DoS guard: `MAX_IPC_ENVELOPE_BYTES = 256 KiB` ceiling on the serialized envelope (empirically <16 KiB at full active-dim coverage at rubric v1.0.1; the cap leaves headroom for future N/A justification growth).

## §3. Strengthened — tamper resistance posture (Hard Guarantee §5)

Pre-Track-A (master `52c003e` after BEAAA-2175 PR-B): the scoring callee ran on the **API edge** inside `_production_score_envelope_factory` → `ScoreRuntime.compute_envelope` → `rubric.scoring.score`. The API edge has no egress scope around the scoring step.

Post-Track-A (this patch): the scoring callee runs **inside the Scorer subprocess**, which opens `ScopedEgressAllowlist([])` (deny-all) for the lifetime of `run_scorer`. The agent-under-scan cannot route adversarial content into a scoring import graph that has any network surface — every egress attempt raises `EgressNotAllowed` at the transport layer.

Hard Guarantee §5 (tamper resistance) is therefore strictly STRENGTHENED by this patch, not weakened. The §Approval-gates "Founder sign-off required for tamper-resistance reduction" row is NOT triggered (this is a strengthening, not a reduction).

## §4. Unchanged — binding envelope dict shape

The §2.2 envelope dict the API-edge `score_envelope_factory` returns to `ScanRuntime.kickoff` for persistence to `scan_log` and signing via `compute_snapshot_hash` is byte-identical to the v1.2.x shape:

* Same 14 fields enumerated at ADR-0017 addendum-1 §2.2 (agent_id, are_total, band, confidence_below_adjudication_threshold, corpus_version, evidence_refs, model_versions, partial_completion, prior_scan_refs, purpose, rubric_version, run_timestamp, scanner_version, seed, subscores) + the optional `prior_scan_refs_truncated` cap sentinel.
* Same JCS canonicalization (RFC 8785-style; SHA-256 hash).
* Same `evidence_refs[]` row-shape (rubric_point_id, test_id, artifact_uri, verdict, weight, na_justification, na_justification_key + the BEAAA-796 / v1.0.3 P4 review_state / tier rows attached via `harness.verdict_envelope.attach_p4_evidence_metadata`).
* Same `band_letter` → `band` remap inherited from BEAAA-2175 PR-B (`band_letter` is CLI-internal; `band` is the binding §2.2 field).

Underwriter rating engine reads the same dict it always has. Customer-facing surfaces (the v1.3.0 telemetry-shim OTel span attribute names, the v1.4.0 envelope-additive fields) are unaffected — those layers run downstream of this projection.

## §5. AG-A1..AG-A6 verification surface

| Gate | What | Where verified | Result |
| --- | --- | --- | --- |
| AG-A1 | `ScoreRuntime.compute_envelope` projects §2.2 envelope dict with all 14 fields populated (no `_phase1_pending` placeholders) | `tests/test_beaaa2182_a_scorer_wiring.py::TestAGA1ScoreRuntimeProjection` (4 tests) | PASS |
| AG-A2 | N=5 spread = 0 on `are_total` and every subscore against the synthetic-v0 row set | `scripts/beaaa2182_a_calibration.py` → `tools/calibration-deltas/2026-06-05-beaaa2182-track-a-n5.{json,md}` | PASS (spread = 0; unique canonical = 1) |
| AG-A3 | `ALLOWED_SCORE_ENVELOPE_FIELDS` allow-list rejects every non-listed field on serialize and on receive | `tests/test_beaaa2182_a_scorer_wiring.py::TestAGA3AllowList` (7 tests) | PASS |
| AG-A4 | `("err", type, msg)` tuple sentinel round-trips through `WorkerChannel.send_score_envelope` / `recv_score_envelope` | `tests/test_beaaa2182_a_scorer_wiring.py::TestAGA4ReturnChannelSentinel` (5 tests) | PASS |
| AG-A5 | `scripts/check_no_apiedge_score.py` finds zero forbidden `api.score_runtime` imports; Scorer-startup test-mode assertion fires when `api.score_runtime` is in sys.modules | `tests/test_beaaa2182_a_scorer_wiring.py::TestAGA5RuntimeAssertion` (3 tests) | PASS |
| AG-A6 | `ARE_SCORER_REAL=off` rolls back to the master `52c003e` `_phase1_pending` envelope | `tests/test_beaaa2182_a_scorer_wiring.py::TestAGA6FlagOffRollback` (5 tests) | PASS |

26/26 fixture tests pass on the working tree.

## §6. Subprocess-end-to-end golden calibration — deferred to CTO merge gate

In-process AG-A2 (this patch) proves Scorer-side projection determinism. The subprocess-end-to-end N=5 calibration against the frozen golden roster (B1/G1/G2/M1/M2/M3) requires the full `bin/are-scan` CLI harness running against the live probe corpus + applicability fixtures. That is the **next pre-merge step** the CTO will run before granting Track A merge co-sign per the next-steps row in CTO co-sign comment `9840343c`. The byte-equality contract is: same `rubric.scoring.score` callee → same `harness.contract_envelope.to_contract_output` projection → same `project_to_envelope_section_2_2` selector → byte-identical envelope.

## §7. Approval gates for v1.5.0

* CTO authorship — granted (this DRAFT).
* CTO merge co-sign — pending on:
  * OQ-1 target-version confirmation.
  * Subprocess-end-to-end golden calibration spread = 0 (per §6).
  * AG-A5 CI gate green on the merge commit.
* UW operational co-sign — pending on OQ-2 classification confirmation. The op-seat is the binding co-signer for "contract additive minor" rows per v1.3.0 §7.
* HoUW countersign — NOT triggered (no persistence-semantics change, no verdict semantics change, no enum widening, no selection_rule change).
* Founder gate — NOT triggered (tamper resistance is STRENGTHENED, not reduced; no pillar weight change; no canonical-term change; no covered-peril change).

---

**End of patch DRAFT.**
