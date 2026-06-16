---
artifact_type: adr
owner: cto
persona_audience: internal
phase: 2
canonical_terms_only: true
focus_claim: "Phase 2.0 turns every bound policy's runtime logs into peril-tagged exposure evidence via a push-mode ingest gateway, a normalizer producing Log Event Schema v1, a P4+P5 detector framework reusing pre-bind probe substrate, and three downstream sinks (claims queue, per-customer rescore feed, portfolio calibration feed), all outside `are-scanner-api-v1@v1.0` Output."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1", "log-event-schema-v1 (draft, BEAAA-5816)"]
ticket_refs: ["BEAAA-5815", "BEAAA-5812", "BEAAA-5816", "BEAAA-5817", "BEAAA-5818", "BEAAA-5819", "BEAAA-5745", "BEAAA-5760", "BEAAA-5761", "BEAAA-5752", "BEAAA-5755", "BEAAA-5756", "BEAAA-5757", "BEAAA-5754"]
proof_points_cited:
  - "BEAAA-5812 plan v0.1 accepted by Founder 2026-06-16T12:32:23Z (interaction 628de792-…) — doc phase-2-log-telemetry-plan rev b1995b50"
  - "BEAAA-5760/5761 peril-aligned underwriting rollup landed (pure export, Δ=0 by construction)"
  - "BEAAA-5745 peril-coverage audit — pre-bind probe substrate inventory (P4: BEAAA-5752 helpers; P5: BEAAA-5757 helpers; P2: BEAAA-5756 stub; P1: BEAAA-5755 helpers)"
  - "BEAAA-5754 live-probe substrate (ProbeRuntime) — shape reuse for detector verdict envelope"
non_score_moving: true
contract_output_shape_unchanged: true
classification: design-only (no code change in this ticket; implementation children classified per supervision-v1.1 table in §7)
supervision_contract: scanner-implementation-supervision-v1@v1.1
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted:
  - "Phase 2 build does not start until Phase 1 carrier paper + reinsurance milestones are in writing (per BEAAA-5812 plan, Founder-accepted)."
  - "Log Event Schema v1 (BEAAA-5816) is contract-class — same change-control regime as are-scanner-api-v1; CTO + Scanner Engineer co-author, UW co-sign on any field that feeds scoring."
  - "Any detector that consumes a field that feeds scoring → calibration-delta classification → UW + Actuary + Founder co-sign before implementation."
  - "Pull-mode collector deferred to Phase 2.5 (separate Founder gate)."
  - "Privacy posture C (hybrid schema-driven, per-field redaction policy on the Log Event Schema) — Founder-accepted default; schema-driven enforcement is load-bearing for legal posture."
---

# ADR-0034 — BEAAA-5815 Phase 2.0 Post-Bind Log Architecture

## BLUF

Decision: Phase 2.0 ships **push-mode** log ingestion (signed daily S3 manifest), a **normalizer** producing typed events conforming to **Log Event Schema v1** (contract-class, drafted under BEAAA-5816), a **peril-detector framework** with **P4 + P5 implemented** as the MVP set (reusing pre-bind probe helpers landed under BEAAA-5752 / BEAAA-5757) and **P1 / P2 / P3 / P6 stubbed** with seed pointers from BEAAA-5755 / BEAAA-5756 / BEAAA-5757 / future, three downstream sinks (**claims queue**, **per-customer rescore feed**, **portfolio calibration feed**), and an **observed-vs-predicted appendix** extending the BEAAA-5760 underwriting rollup as a pure export view.

Every surface in this ADR is **outside** `are-scanner-api-v1@v1.0` Output. The pre-bind score, pillar subscores, band, confidence, and `pricing/rates.yaml` rate book remain **byte-stable** — **Δ=0 by construction**.

Classification: this ADR is **design-only**. Implementation children (§7) carry their own classification and N=5 calibration delta per `scanner-implementation-supervision-v1@v1.1`.

**Phase 1 firewall:** No implementation child lands code until (a) the carrier paper is bound and (b) reinsurance is in writing. Per BEAAA-5812 plan §Phasing, accepted by Founder 2026-06-16T12:32:23Z. Children may be created and scoped now under the ~0.5 FTE design carve-out, but no merge to canon master until the firewall lifts.

## 1. Architecture

```
┌─ Customer environment ────────────┐     ┌─ BackBond control plane ──────────────────────────────┐
│                                   │     │                                                       │
│  Agent runtime → log sink         │     │  (1) Ingest gateway                                   │
│  (Datadog / Splunk / CloudWatch / │     │      └─ Push: signed S3 manifest (daily batch)        │
│   OTEL / raw stdout)              │ ──▶ │         · per-customer prefix, KMS-encrypted          │
│                                   │     │         · manifest = signed JSON {sha256[], range}    │
└───────────────────────────────────┘     │         · pull (Phase 2.5) — out of scope here        │
                                          │                                                       │
                                          │  (2) Normalizer                                       │
                                          │      └─ stdin: raw vendor-shaped records              │
                                          │         stdout: typed events conforming to            │
                                          │                 Log Event Schema v1 (BEAAA-5816)      │
                                          │      └─ per-field redaction policy applied here       │
                                          │         (posture C: schema-driven; redact-on-receipt) │
                                          │                                                       │
                                          │  (3) Peril detector framework                         │
                                          │      └─ P4 + P5 implemented (MVP)                     │
                                          │      └─ P1 / P2 / P3 / P6 stubbed (deferred)          │
                                          │      └─ verdict envelope = ProbeVerdict-shaped        │
                                          │         (reuses BEAAA-5754 surface)                   │
                                          │                                                       │
                                          │  (4) Three sinks                                      │
                                          │      ├─ Claims queue (UW / TPA)                       │
                                          │      ├─ Per-customer rescore feed (Actuary)           │
                                          │      └─ Portfolio calibration feed (Actuary)          │
                                          │                                                       │
                                          │  (5) Observed-vs-predicted appendix                   │
                                          │      └─ extends BEAAA-5760 underwriting rollup        │
                                          │         (internal_uw + evidence_appendix variants;    │
                                          │          GET /api/v1/scans/{id}/underwriting_rollup;  │
                                          │          dp variant unchanged — Carrier Rule)         │
                                          │                                                       │
                                          └───────────────────────────────────────────────────────┘
```

### 1.1 Component ownership

| Component | Primary owner | Co-sign | Lands in |
|---|---|---|---|
| Ingest gateway (push, S3) | CTO | Scanner Engineer | `tools/post-bind-ingest/` (new package, outside `tests/`) |
| Normalizer + Log Event Schema v1 | CTO + Scanner Engineer | UW on score-feeding fields | Schema in `contracts/log-event-schema-v1.md` (BEAAA-5816); normalizer in `tools/post-bind-ingest/normalizer/` |
| Peril detector framework | CTO + Scanner Engineer | UW + Actuary + Founder if a detector feeds scoring | `tools/post-bind-ingest/detectors/` (P4 / P5 impl); stubs as `*_stub.py` siblings |
| Claims queue sink | UW (consumer) + CTO (emitter) | counsel | Emitter in `tools/post-bind-ingest/sinks/claims_queue.py`; consumer = BEAAA-5817 |
| Per-customer rescore feed | Actuary (consumer) + CTO (emitter) | UW | Emitter in `tools/post-bind-ingest/sinks/rescore_feed.py`; consumer = BEAAA-5818 |
| Portfolio calibration feed | Actuary (consumer) + CTO (emitter) | UW + Founder | Emitter in `tools/post-bind-ingest/sinks/calibration_feed.py`; consumer = BEAAA-5818 |
| Observed-vs-predicted appendix | CTO + CMO (render copy) | UW | Extends `tools/portal_v1/portal.py::_render_internal_report` |

### 1.2 Implementation site rationale

Phase 2.0 lands as a **new package `tools/post-bind-ingest/`**, parallel to `tools/portal_v1/` and `tools/are-connector/`. Reasons:

1. **Phase 1 firewall.** A separate package keeps the post-bind code path from polluting the pre-bind probe / scoring surface during the firewall window. Auditor flags any import edge from `tools/portal_v1/` or the canonical scoring code to `tools/post-bind-ingest/`.
2. **Reuse without coupling.** Detector code reuses the pre-bind probe **helper modules** (`tests/_p4_helpers.py`, `tests/_p5_helpers.py`, `tests/probe_runtime_stub.py`) by **copy-and-adapt** at the helper level, not by runtime import. Pre-bind helpers are CI-time substrate (mock agents, regex extractors). The runtime detector layer needs the same shape (NumericClaimExtractor, RefusalClassifier) but operates on production log events, not on `BaseHTTPRequestHandler` mocks. The connector live-probe `ProbeRuntime` (BEAAA-5754) is the closer architectural sibling — the detector verdict envelope inherits the `ProbeVerdict (status, reason, evidence)` shape.
3. **Deployment isolation.** Phase 2.5 pull-mode (separate Founder gate) will run inside customer VPC. Phase 2.0 push-mode runs in BackBond control plane. Same package, different deployment target — clean lift-and-shift later.

## 2. Ingest gateway (push, MVP)

### 2.1 Wire shape

| Surface | Specification |
|---|---|
| Endpoint | `s3://backbond-post-bind-ingest/{customer_id}/manifests/{YYYY-MM-DD}/manifest.json` |
| Manifest body | JSON; signed by customer's per-bind ingest key; envelope `{ customer_id, agent_id, day_utc, log_objects: [{sha256, byte_range, content_type, source_kind}], signature, signature_alg }` |
| Object store | KMS-encrypted (per-customer key); object keys under `s3://backbond-post-bind-ingest/{customer_id}/raw/{YYYY-MM-DD}/{object_sha256}` |
| Cadence | Daily batch (00:00–24:00 UTC of customer's bind region) |
| Auth | Per-customer ingest credential issued at bind time; rotation = manual re-bind in Phase 2.0; automated in Phase 2.1 |
| Failure mode | Manifest signature failure → reject, alert customer, do **not** ingest objects; partial manifest → ingest verified subset, queue rest for next cadence |

### 2.2 Carrier Rule on ingest

Manifest receipt does **not** open or pay a claim. It produces a queued candidate event that requires (a) the normalizer accepting the records under Log Event Schema v1 and (b) a peril detector firing above customer-and-peril-specific threshold. Per BEAAA-5812 plan §Carrier Rule check, the post-bind log program is framed as evidence that **helps carriers underwrite and pay claims**, not as a mechanism for post-hoc denial. Claims-trigger thresholds are owned by UW + counsel under BEAAA-5817.

### 2.3 Privacy posture (C — hybrid schema-driven)

Customer agent logs frequently contain PII and customer-of-customer data. The Founder-accepted posture is **C — hybrid schema-driven**: the Log Event Schema v1 carries **per-field redaction policy** declared at the schema layer, and redaction is applied **on receipt** by the normalizer (between ingest and the typed-event store).

- The schema marks each field one of: `cleartext_ok`, `redact_pii`, `redact_secret`, `hash_only`, `drop`.
- The normalizer enforces the policy with deterministic, replayable transforms (same input → same output → same `transcript_sha`).
- The connector secret-family redaction rules (BEAAA-5740 substrate) are the floor: anything matching the connector's `_REDACTION_PATTERNS` is `redact_secret` regardless of field policy.
- Raw object retention: BackBond retains raw objects for the rolling N days needed for legal compliance and reproducibility, then enforces TTL deletion. The N value is a UW + counsel + Founder decision under BEAAA-5817 / BEAAA-5819; this ADR does not set it.

## 3. Normalizer + Log Event Schema v1

### 3.1 Schema contract (placeholder; full spec under BEAAA-5816)

| Event family | Shape sketch | Origin |
|---|---|---|
| `agent_session_start` | `{customer_id, agent_id, session_id, t_start, agent_version, scanner_version_at_bind}` | new |
| `llm_completion` | `{session_id, turn_index, prompt_sha, completion_sha, model, tokens_in, tokens_out, latency_ms, refusal_class?}` | new |
| `tool_call` | `{session_id, turn_index, tool_name, args_sha, return_sha, tool_source_kind, ok}` | mirrors pre-bind `MockAgentPolicy` action shape (BEAAA-5756 stub) |
| `tool_source_reference` | `{session_id, turn_index, source_kind, source_id, content_sha}` | mirrors `MockToolSource` shape (BEAAA-5752 helpers) |
| `escalation_to_human` | `{session_id, turn_index, reason_code, transcript_sha}` | new |
| `claim_candidate_event` | `{session_id, peril, severity, evidence_event_refs, detector_version}` | output of detector framework |
| `error` | `{session_id, t, code, message_sha, recoverable}` | new |

The full schema is authored under BEAAA-5816 (CTO + Scanner Engineer; UW co-sign on `claim_candidate_event` and on any field that ever feeds scoring). The schema document lands at `contracts/log-event-schema-v1.md` and is byte-locked on publish under the same change-control as `are-scanner-api-v1@v1.0`.

### 3.2 Normalizer contract

```
input:  iterable[raw_vendor_record]
output: iterable[LogEventSchemaV1Event]
sideeffect: per-field redaction applied; transcript_sha computed over canonical
            post-redaction JSON.
```

Determinism: same input → same output (no clocks, no RNG, no live LLM). Replayable from raw object plus schema version. Vendor-shape adapters (Datadog / Splunk / CloudWatch / OTEL / raw stdout) live as separate modules under `tools/post-bind-ingest/normalizer/adapters/`.

## 4. Peril detector framework

### 4.1 Verdict envelope (shape reuse from BEAAA-5754 ProbeVerdict)

```
DetectorVerdict = {
    detector_id: str,                    # e.g. "P4.numeric_claim_tool_mismatch"
    peril: Literal["P1","P2","P3","P4","P5","P6"],
    status: Literal["pass","fail","error"],
    reason: str,                         # taxonomy mirrors ProbeVerdict.reason
    severity: Literal["low","medium","high","critical"],
    evidence: dict,                      # event refs + extractor outputs
    detector_version: str,
    schema_version: str,                 # log-event-schema-v1.x
    seed: int | None,
}
```

`status` mirrors `pass | fail | error` from `ProbeVerdict` so downstream sinks (claims queue, rescore feed) can reuse the contract-simple shape. The detailed reason taxonomy lives in `reason`.

### 4.2 MVP detector set — P4 + P5 (Founder-accepted default)

| Detector | Pre-bind probe seed | Runtime source signal | Status |
|---|---|---|---|
| `P4.numeric_claim_tool_mismatch` | `tests/_p4_helpers.py::NumericClaimExtractor` (BEAAA-5752) + `tests/test_grounded_factuality.py` | `llm_completion.completion_sha` + paired `tool_call.return_sha` for the same `(session_id, turn_index)`: extract numeric claim, compare against tool return | **implemented** |
| `P4.hallucinated_citation` | `tests/_p4_helpers.py::CitationExtractor` (BEAAA-5752) + `tests/test_citation_grounded.py` | `llm_completion` citation extraction + `tool_source_reference` resolution: assert citation resolves and supports the claim | **implemented** |
| `P5.direct_injection_landed` | `tests/_p5_helpers.py::RefusalClassifier` (BEAAA-5757) + `tests/test_prompt_injection_direct.py` | injection pattern observed in user input + non-refusal completion + downstream `tool_call` with sensitive scope | **implemented** |
| `P5.indirect_injection_landed` | `tests/_p5_helpers.py::RefusalClassifier` + `tests/test_prompt_injection_indirect.py` | injection pattern observed in `tool_source_reference.content_sha` + downstream `tool_call` matching injection intent | **implemented** |

**Rationale for MVP = P4 + P5** (per BEAAA-5812 plan, Founder-accepted):
- Highest signal-to-noise per event volume.
- Lowest legal-interpretation surface — P4 (hallucination) and P5 (injection) trigger on observable mechanical patterns, not on inferred state of mind.
- Cleanest reuse of pre-bind probe helper code (regex extractors, refusal classifier) — no judge model in the runtime path (judge models are rule-change per supervision-v1.1 row 31).

### 4.3 Stubbed detectors (P1 / P2 / P3 / P6)

Stubs land as `tools/post-bind-ingest/detectors/p{1,2,3,6}_stub.py` with **no production wiring** (sink emitters import only the implemented detector modules). Stubs carry pointers to the pre-bind probe code that will seed each:

| Stub | Pre-bind seed | Phase 2.1 owner | Open question |
|---|---|---|---|
| `P1.bias_outcome_divergence` | `tests/test_beaaa199_p2_emit.py` + canonical BIAS_FAIRNESS suite | Actuary + counsel | Cohort labeling without inferring protected class from log data — legal-sensitive; in Phase 2.1 scoping under BEAAA-5818 |
| `P2.tool_misuse_runtime` | `tests/probe_runtime_stub.py` (BEAAA-5756) + `tests/test_stop_instruction.py` + `tests/test_tool_scope_escape.py` | CTO + Scanner Engineer | Detection threshold for "runaway loop" on real session lengths (pre-bind probes use M=8 cap; production sessions vary) |
| `P3.prompt_injection_multiturn` | `tests/_p5_helpers.py` Crescendo substrate (BEAAA-5757) | CTO + Scanner Engineer | Multi-turn linking requires session-spanning state (pre-bind probes are single-shot) |
| `P6.operational_anomaly` | (no pre-bind probe; new) — cost / latency / refusal-rate anomalies vs per-customer baseline | CTO + Actuary | Baseline establishment window per customer (rolling N days); cold-start handling |

Activating any stub is a separate ticket and follows the supervision-v1.1 classification table. If the activated detector feeds scoring (rescore feed → `rates.yaml`), it is **rule-change** requiring UW + Actuary + Founder co-sign.

### 4.4 Wrap-don't-fork posture

The detectors **wrap** existing pre-bind probe helpers (BEAAA-5752 / 5755 / 5756 / 5757) at the **shape** level — same extractor and classifier code, applied to a different input stream. No upstream eval framework (AdvBench / JailbreakBench / HaluEval / FActScore / TruthfulQA) is bundled into the post-bind runtime; those remain CI-time corpora for pre-bind probes. The post-bind runtime sees only the customer's own log stream.

## 5. Three sinks

### 5.1 Claims queue sink (consumer = BEAAA-5817)

```
emitter:  tools/post-bind-ingest/sinks/claims_queue.py
trigger:  DetectorVerdict.status == "fail" AND severity meets per-peril threshold
output:   { claim_candidate_id, customer_id, agent_id, peril, severity,
            detector_id, detector_version, evidence_event_refs, transcript_sha,
            t_emitted }
contract: thresholds owned by UW + counsel under BEAAA-5817
```

The sink emits **claim candidate events**, not claim decisions. A UW or TPA reviews and opens / declines the claim per wording. The sink is fail-closed: an emitter error never auto-opens a claim. Carrier Rule: language on this surface stays internal; the customer-facing artifact (BEAAA-5819 / CMO) frames this as "evidence that helps your carrier pay the claim correctly," never "evidence we used to deny your claim."

### 5.2 Per-customer rescore feed (consumer = BEAAA-5818)

```
emitter:  tools/post-bind-ingest/sinks/rescore_feed.py
trigger:  rolling-window observed peril-frequency on the customer's traffic
output:   { customer_id, peril, window_days, observed_rate,
            predicted_rate_at_bind, divergence, detector_versions_seen,
            schema_version, t_emitted }
contract: rescore methodology owned by Actuary under BEAAA-5818
```

The feed is **input to** Actuary methodology. It does **not** mutate the customer's BackBond Score, does not write `pricing/rates.yaml`, and does not assert a divergence threshold of its own. Threshold and rescore math are downstream Actuary decisions; any change that feeds back into scoring is **calibration-delta** and requires UW + Actuary + Founder co-sign per BEAAA-5812 plan §Governance and supervision-v1.1.

### 5.3 Portfolio calibration feed (consumer = BEAAA-5818)

```
emitter:  tools/post-bind-ingest/sinks/calibration_feed.py
trigger:  quarterly aggregation across all customers in a peril/segment cohort
output:   { cohort_id, peril, observed_rate, rates_yaml_predicted_rate,
            divergence, N_customers, sample_period, t_emitted }
contract: rates.yaml update owned by Actuary + UW + Founder under BEAAA-5818
```

Same posture as 5.2 at the portfolio layer. No automatic `rates.yaml` write. The feed produces evidence; the human authority signs the change.

## 6. Observed-vs-predicted appendix (BEAAA-5760 rollup extension)

### 6.1 Scope

Extend the BEAAA-5760 peril-aligned underwriting rollup with an **observed-vs-predicted block**. The block renders only on the `internal_uw` and `evidence_appendix` HTML variants and on `GET /api/v1/scans/{scan_id}/underwriting_rollup` JSON. The `dp` customer-facing variant remains **byte-stable** — Carrier Rule, mirrors the BEAAA-5761 stance.

### 6.2 Shape (sketch — final shape lands in the extension implementation ticket)

```json
"observed_vs_predicted": {
  "schema_version": "log-event-schema-v1.x",
  "window_days": 90,
  "per_peril": {
    "P4": {
      "predicted_rate_at_bind": 0.02,
      "observed_rate": 0.025,
      "divergence": 0.005,
      "divergence_band": "within_tolerance",
      "detector_versions_seen": ["P4.numeric_claim_tool_mismatch@0.1.0",
                                  "P4.hallucinated_citation@0.1.0"],
      "N_candidate_events": 14
    },
    "P5": { ... }
  },
  "untagged_event_count": 0,
  "data_completeness": {
    "manifests_received": 89, "manifests_expected": 90,
    "completeness_band": "complete"
  }
}
```

### 6.3 Δ=0 guarantee — defense-in-depth

The appendix is **pure export**. It does **not**:

- read `pricing/rates.yaml` (no premium-calculation surface);
- read `rubric.yaml` (no rubric weights);
- mutate `are_total`, any pillar subscore, band, or confidence in the rollup payload;
- introduce a new rubric_point_id;
- introduce a new judge model.

The appendix **does** read the scanner's per-peril predicted rates at bind from a new field on the rollup. That field is computed at bind time and persisted on the score record; reading it post-bind does not alter scoring. The BEAAA-5761 `_build_peril_rollup` defense-in-depth pre/post snapshot guard remains in place; the extension adds the appendix downstream of the guard.

Classification per supervision-v1.1: same row as BEAAA-5761 (row 33, new export endpoint analog) — **rule-execution**, no UW / Actuary / Founder co-sign for the rendering itself. (Any change that would let the appendix mutate score is **rule-change** and would not ship under this row.)

### 6.4 Carrier Rule on copy

- `<h2>` heading: **"Observed-vs-predicted (post-bind log evidence)"** — internal-only variants.
- Framing constant: **"Evidence to help the carrier price the renewal correctly. Not used for retroactive claim adjudication."** (CMO co-sign required on the constant; mirrors the BEAAA-5761 `ROLLUP_BIAS_NOTE` pattern.)
- `dp` variant: **no observed-vs-predicted content rendered**. Asserted by a byte-stability test parallel to `test_dp_report_byte_stable_after_rollup_feature`.

## 7. Implementation children — rule-execution vs calibration-delta

Phase 2.0 implementation breaks into the children below. Each child carries its own supervision-v1.1 classification and its own N=5 calibration delta receipt (where applicable).

| Child (to be opened on firewall lift) | Scope | Supervision-v1.1 row | Co-sign |
|---|---|---|---|
| **(a) Ingest gateway — push** | S3 manifest + signature + adapter shell | row 17 "Customer onboarding harness for a new customer archetype" (analog: new ingestion path) | **rule-execution** — CTO unilateral within budget |
| **(b) Log Event Schema v1 publish** | `contracts/log-event-schema-v1.md` (drafted under BEAAA-5816) | new contract publication — same regime as `are-scanner-api-v1` | **CTO + Scanner Engineer co-author + UW co-sign** on any field that feeds scoring |
| **(c) Normalizer + adapters** | normalizer shell + Datadog / OTEL / raw-stdout adapter | row 1 "Implement a new probe inside an existing rubric_point_id" (analog: new evidence emitter in a known peril family) | **rule-execution** — N=5 on golden set required; UW co-sign if `claim_candidate_event` shape touched |
| **(d) P4 detector implementation** | `P4.numeric_claim_tool_mismatch` + `P4.hallucinated_citation` reusing BEAAA-5752 extractors | row 1 "Implement a new probe inside an existing rubric_point_id" | **rule-execution** — N=5 on golden set required; emits claim_candidate_event only, no score path |
| **(e) P5 detector implementation** | `P5.direct_injection_landed` + `P5.indirect_injection_landed` reusing BEAAA-5757 classifier | row 1 same as above | **rule-execution** — N=5 on golden set required |
| **(f) Claims-queue sink emitter** | `sinks/claims_queue.py` emitter (consumer = UW under BEAAA-5817) | row 33 "CLI flag addition / new export endpoint analog" | **rule-execution** — UW co-sign on the queue contract |
| **(g) Rescore feed emitter** | `sinks/rescore_feed.py` emitter (consumer = Actuary under BEAAA-5818) | row 33 same as above — **read-only** | **rule-execution** if read-only; **rule-change** the moment a detector feeds back into scoring |
| **(h) Calibration feed emitter** | `sinks/calibration_feed.py` emitter (consumer = Actuary under BEAAA-5818) | row 33 same as above — **read-only** | **rule-execution** if read-only; portfolio `rates.yaml` updates from this feed are **rule-change** requiring UW + Actuary + Founder |
| **(i) Observed-vs-predicted appendix** | extend `tools/portal_v1/portal.py::_render_internal_report`; mirror BEAAA-5761 defense-in-depth guard | row 33 "new export endpoint analog" | **rule-execution** — CMO co-sign on copy constants; **dp** byte-stability test |
| **(j) Stub modules** | `p1_stub.py` / `p2_stub.py` / `p3_stub.py` / `p6_stub.py` — no production wiring | row 18 "Refactor that doesn't touch behavior" | **rule-execution** — N=5 regression check only |

**Lines that cross into calibration-delta** (any of these triggers UW + Actuary + Founder co-sign before merge):

1. Any change that lets a detector verdict mutate `are_total`, pillar subscores, band, confidence, or `pricing/rates.yaml`.
2. Any change to the Log Event Schema v1 field set that feeds scoring (rule-change at the contract layer, mirrors `are-scanner-api-v1` regime).
3. Activating any stub detector (P1 / P2 / P3 / P6) such that its output reaches the rescore or calibration feed.
4. Any change to the observed-vs-predicted appendix that lets it mutate score state (today: structurally impossible; tomorrow: a contract breach).
5. Any change to a judge model used inside a detector. Today's P4 + P5 detectors are **judge-free** (deterministic extractors); adding a judge later is rule-change per supervision-v1.1 row 31.

## 8. Phase 1 firewall — explicit

Per BEAAA-5812 plan v0.1, Founder-accepted 2026-06-16T12:32:23Z, and the BEAAA-5815 brief:

- **No implementation child lands code in canon master** until (a) the carrier paper is bound and (b) reinsurance is in writing. The CEO maintains the firewall-lift checklist on BEAAA-5812.
- Design work (this ADR, BEAAA-5816 schema draft, BEAAA-5817 wording draft, BEAAA-5818 methodology draft, BEAAA-5819 comms draft) runs in parallel within the Founder-approved ~0.5 FTE design carve-out.
- No Phase 1 gating item (carrier paper close, reinsurance in writing, LOIs, wording draft 2, Scanner v0.1 live, CFO) gets reprioritized to make room for Phase 2 build. If conflict arises, CTO halts the Phase 2 child and escalates to CEO.
- The firewall lift is a CEO + Founder gate, recorded on BEAAA-5812 with the canonical date. Implementation children change `status` from `blocked` to `todo` only after that gate.

## 9. Reuse map — pre-bind probe code

Source modules and their post-bind reuse role:

| Pre-bind source | Reuse role in Phase 2.0 |
|---|---|
| `tests/_p4_helpers.py` (BEAAA-5752): `NumericClaimExtractor`, `CitationExtractor`, `MockToolSource`, `MockGroundedAgent`, `pass_at_k` | Extractors lift into `detectors/p4_numeric.py` and `detectors/p4_citation.py`. Mock substrates stay in `tests/` — runtime sees real log events, not mocks. `pass_at_k` reused as reliability metric for detector verdict aggregation across rolling windows. |
| `tests/_p5_helpers.py` (BEAAA-5757): `RefusalClassifier`, injection patterns from AdvBench / JailbreakBench / AgentDojo, `pass_at_k` | `RefusalClassifier` lifts into `detectors/p5_injection.py`. Injection-pattern recognition runs against `tool_source_reference.content_sha` and `llm_completion.prompt_sha`. CI corpora stay in `tests/`. |
| `tests/probe_runtime_stub.py` (BEAAA-5756): `MockAgentPolicy` shape, `ProbeTrial`, `ProbeResult` | Action shape (`tool_call` / `final_reply` / `halt`) is the canonical inspiration for the Log Event Schema v1 `tool_call` and `llm_completion` event types. P2 stub seeds detector framework with the same trial/result envelope. |
| `tests/test_p1_*` family (BEAAA-5755 helpers) | Seeds for the **stubbed** P1 (BIAS) detector. Bias detection in production logs is a Phase 2.1 child with counsel involvement (legal-sensitive cohort labeling). Stub carries the pointer; activation is rule-change. |
| `tools/are-connector/src/are_connector/probe_runtime.py` (BEAAA-5754): `ProbeRuntime`, `ProbeVerdict`, redaction substrate | Verdict envelope shape (`status`, `reason`, `evidence`) lifts directly into `DetectorVerdict`. Redaction substrate is the floor for the normalizer's per-field redaction policy. |
| `tools/portal_v1/portal.py::_build_peril_rollup` (BEAAA-5761): rollup builder + defense-in-depth invariant guard | The observed-vs-predicted appendix renders inside the rollup. The BEAAA-5761 pre/post score-invariant guard remains in place; the appendix attaches downstream and adds its own assertion that the appendix never mutates any field in the upstream rollup output. |

## 10. Rejected alternatives

- **Bundle post-bind into `tools/portal_v1/`.** Rejected: Phase 1 firewall cleanliness; portal_v1 is already a focused export/view layer; mixing in a runtime event-driven service erodes the surface boundary and confuses Auditor's import-edge checks.
- **Bundle into the connector (`tools/are-connector/`).** Rejected: connector is the pre-bind live-probe carrier (BEAAA-5754); it operates against customer endpoints for scanning, not against customer log streams. Conflating the two muddles audit trails (probe transcript vs production log event), and connector's egress allowlist is the wrong shape for receiving inbound manifests.
- **Pull-mode as Phase 2.0 MVP.** Rejected: Pull-mode touches customer infra and changes the security review surface materially. Push-mode minimizes BackBond build effort, minimizes per-customer trust requirements, and keeps the MVP shippable inside the design carve-out. Pull-mode is BEAAA-5812 Phase 2.5, separate Founder gate.
- **Include P2 (tool-misuse) in MVP.** Rejected for MVP: P2 detection in production logs requires session-spanning state and per-customer baseline establishment (real sessions vary in length where pre-bind probes use M=8 cap). P2 is a clean Phase 2.1 activation under (d) when the framework is proven on P4 + P5. The CEO plan considered "P4 + P5 OR include P2" — Founder accepted the P4 + P5 default.
- **Include P1 (bias) in MVP.** Rejected for MVP: P1 detection in production logs requires cohort labeling, which is legal-sensitive and requires counsel + Actuary involvement. P1 is Phase 2.1 under BEAAA-5818.
- **Direct write to `pricing/rates.yaml` from the calibration feed.** Rejected: violates the BEAAA-5812 plan §Governance co-sign matrix (portfolio `rates.yaml` update requires UW + Actuary + Founder), and would let a software path silently move premium pricing. The feed produces evidence; the human authority signs the change.
- **In-line judge model in detectors.** Rejected: judge-model changes are rule-change per supervision-v1.1 row 31, requiring CTO co-sign and ADR. P4 + P5 MVP detectors are judge-free (deterministic extractors / regex classifiers). When a future detector needs a judge, it is a separate ADR.
- **Push raw object retention to S3 lifecycle rule only.** Rejected: per BEAAA-5812 plan §Privacy posture, retention TTL is a UW + counsel + Founder decision (BEAAA-5817 / BEAAA-5819). The S3 lifecycle rule encodes the decision but does not own it.

## 11. Open follow-ups (carry into firewall-lift checklist on BEAAA-5812)

- **BEAAA-5816** — Log Event Schema v1 contract draft. Blocked on this ADR, unblocked on land.
- **BEAAA-5817** — Claims-trigger thresholds + draft wording clause. Blocked on this ADR + on schema v1 detector list.
- **BEAAA-5818** — Per-customer rescore methodology + observed-vs-predicted appendix spec. Blocked on this ADR + on schema v1 detector list.
- **BEAAA-5819** — CMO comms + reinsurance pre-read. Blocked on capacity carve-out (already approved) + on this ADR for the Carrier Rule framing.
- **Schema v1 redaction-policy field set** (BEAAA-5816 sub-decision). The schema must mark every field with one of `cleartext_ok | redact_pii | redact_secret | hash_only | drop` at publish time. Posture C is contingent on this.
- **P6 baseline window** (Phase 2.1 sub-decision). Cold-start and rolling-window handling for the operational-anomaly detector. Not blocking Phase 2.0.

## 12. Self-audit (per cto-os.md Supreme Law)

1. **Non-score-moving statement explicit.** YES — front-loaded in BLUF. §5.2/5.3 and §6 reiterate. Every implementation child in §7 cites the row classification; lines that would cross into calibration-delta are enumerated explicitly. Passed.
2. **`are-scanner-api-v1@v1.0` cited.** YES — Output shape stays byte-stable; the appendix in §6 is outside the contract Output, mirroring BEAAA-5761. Passed.
3. **Co-sign matrix correct.** YES — schema-shaped sections require CTO + Scanner Engineer co-sign (BEAAA-5816 child). UW co-sign required on any field that feeds scoring. Founder + UW + Actuary required on any change crossing calibration-delta (enumerated in §7). CMO co-sign on copy constants in §6. Passed.
4. **Supervision-v1.1 row cited per implementation child.** YES — §7 table maps each child to the supervision-v1.1 row. Passed.
5. **Scope tight.** YES — §10 enumerates the rejected alternatives; pull-mode, P2 / P1 in MVP, in-line judges, and direct `rates.yaml` writes are all out of scope. Passed.
6. **Glossary check (canon).** YES — BackBond, BackBond Scanner, ARE Scanner (legacy term in code paths and contract filename per glossary grandfathering), GOVERNANCE / ROBUSTNESS / BIAS_FAIRNESS / OPS pillar naming. Passed.
7. **Carrier Rule.** YES — §2.2, §5.1, §6.4 all anchor on "evidence to help carriers underwrite and pay claims correctly," never on "evidence to deny claims post-hoc." `dp` variant byte-stable. Render-copy constants require CMO co-sign. Passed.
8. **Wrap-don't-fork.** YES — detectors wrap pre-bind probe helper shapes; no upstream eval framework bundled into the runtime; CI corpora stay in `tests/`. Passed.
9. **Phase 1 firewall named explicitly.** YES — §8. No implementation child lands code until carrier paper + reinsurance in writing. Passed.
10. **Pre-existing pre-bind probe code identified for reuse.** YES — §9 reuse map enumerates BEAAA-5752 (P4 helpers), BEAAA-5755 (P1 family), BEAAA-5756 (P2 stub), BEAAA-5757 (P5 substrate), BEAAA-5754 (live-probe ProbeRuntime). Passed.
11. **N=5 calibration delta.** N/A on this ADR by construction — it is a design document with no code change. Each implementation child in §7 carries its own N=5 receipt requirement. The note is here to pre-empt Auditor flagging the receipt as missing.
12. **First claim is reputation-defining.** YES — the BLUF claim is the **Δ=0-by-construction** guarantee on `are-scanner-api-v1@v1.0` Output across all five Phase 2.0 surfaces. Every implementation child in §7 inherits the obligation to defend this claim; any child that cannot defend it is rule-change and routes accordingly. Passed.
