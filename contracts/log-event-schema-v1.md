---
artifact_type: contract
owner: cto
co_authors: ["scanner_engineer"]
persona_audience: internal
phase: 2
canonical_terms_only: true
focus_claim: "Log Event Schema v1.0 is the cross-team binding surface for Phase 2.0 post-bind log telemetry. It declares the canonical typed event families (agent_invocation, llm_completion, tool_call, tool_source_reference, escalation, error, security_event, pii_emission, claim_candidate_event), per-field type / required / redaction-policy / provenance, and the same change-control regime as `are-scanner-api-v1@v1.0`. Privacy posture C — hybrid schema-driven — is realized here: every field carries one of four redaction classes (clear, hash, redact-on-receipt, drop), enforced by the normalizer on receipt. v1.0 has no score-feeding fields by construction; activation of any field into the rescore or calibration feed is a separate rule-change requiring UW + Actuary + Founder co-sign."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1", "log-event-schema-v1@v1.0 (this document)"]
ticket_refs: ["BEAAA-5816", "BEAAA-5815", "BEAAA-5812", "BEAAA-5817", "BEAAA-5818", "BEAAA-5819", "BEAAA-5754", "BEAAA-5752", "BEAAA-5755", "BEAAA-5756", "BEAAA-5757", "BEAAA-5760", "BEAAA-5761"]
proof_points_cited:
  - "BEAAA-5812 Phase 2 plan v0.1 Founder-accepted 2026-06-16T12:32:23Z (interaction 628de792-…) — doc phase-2-log-telemetry-plan rev b1995b50; locks Privacy posture C, MVP detector set P4+P5, design-now-build-after-Phase-1 carve-out."
  - "BEAAA-5815 ADR-0034 landed on canon master at 8d61761 — §3.1 Schema contract (placeholder sketch) cites this contract as the formal publish; §2.3 Privacy posture C sets the per-field redaction-policy floor."
  - "BEAAA-5754 ProbeRuntime / ProbeVerdict — verdict envelope shape reused by `claim_candidate_event` and by the detector framework consuming this schema."
  - "BEAAA-5740 connector secret-family redaction substrate (`_REDACTION_PATTERNS`) — invoked as the floor by the normalizer regardless of declared field class."
  - "BEAAA-5752/5757 P4+P5 pre-bind probe helpers — extractor and classifier shape inherited by the runtime detectors that consume this schema."
non_score_moving: true
contract_output_shape_unchanged: true
version: "v1.0"
prior_version: null
supervision_contract: "scanner-implementation-supervision-v1@v1.1"
self_audit_passed: true
escalations_noted:
  - "Phase 1 firewall: this document is the design publish of the schema. No normalizer / detector / sink implementation lands in canon master until (a) carrier paper is bound and (b) reinsurance is in writing, per BEAAA-5812 plan v0.1 §Phasing."
  - "Privacy posture C is load-bearing for legal posture — any change to the redaction-policy class of an existing field is a contract bump under §Change control."
  - "v1.0 has no score-feeding fields. Any future field that flows into the rescore feed (BEAAA-5818 emitter) or calibration feed becomes score-feeding and the change is rule-change under supervision-v1.1 (UW + Actuary + Founder co-sign)."
  - "Activation of any of the stubbed-detector inputs (P1 BIAS, P2 TOOL_MISUSE, P3 MULTITURN_INJECTION, P6 OPERATIONAL_ANOMALY) into the score path follows the same rule-change track."
---

# Contract: Log Event Schema — v1.0

**Status:** draft for co-sign. CTO owns; Scanner Engineer co-authors on field semantics. Drafted on BEAAA-5816 under the parent program BEAAA-5812. ADR authority: BEAAA-5815 ADR-0034 (canon `8d61761`).
**Op-seat co-sign required:** Scanner Engineer (field semantics).
**Score-path co-sign required:** Underwriter — **on any future v1.x.y change** to a field consumed by the rescore feed (`sinks/rescore_feed.py`) or calibration feed (`sinks/calibration_feed.py`). **v1.0 has no score-feeding fields**, so UW co-sign on v1.0 is courtesy, not required.
**Founder gate required:** on any change that activates a stubbed detector such that its output flows into the rescore or calibration feed, or that lets any event field mutate `are_total` / pillar subscores / band / confidence / `pricing/rates.yaml`.
**Change classification (this publish):** new contract publication — same change-control regime as `are-scanner-api-v1@v1.0`. Byte-locked on publish.
**Phase 1 firewall:** design publish only. No emitter / normalizer / detector / sink code lands until carrier paper bound + reinsurance in writing.

---

## §Version header

```
# Contract: Log Event Schema — v1.0
```

The byte-stable version string is `log-event-schema-v1.0`. Every event MUST carry this string verbatim in `schema_version` until the schema is bumped.

---

## §Scope and posture

This schema defines the **canonical typed event family set** emitted by the Phase 2.0 post-bind log normalizer (per ADR-0034 §1 component 2) and consumed by the peril-detector framework (§4), the three sinks (§5), and the observed-vs-predicted appendix (§6). It is the **binding surface between**:

- BackBond's normalizer (CTO + Scanner Engineer ownership) — emits events conforming to this schema.
- BackBond's peril detectors (CTO + Scanner Engineer) — consume events conforming to this schema.
- BackBond's three downstream sinks (claims queue, rescore feed, calibration feed) — observe events conforming to this schema.
- BackBond's observed-vs-predicted appendix (BEAAA-5760/5761 rollup extension) — reads aggregated detector verdicts derived from events conforming to this schema.

**Privacy posture C — hybrid schema-driven** (Founder-accepted on BEAAA-5812 plan v0.1, 2026-06-16T12:32:23Z): every field declared below carries one of four **redaction policy classes**. The normalizer enforces the policy **on receipt** between ingest and the typed-event store. Per-field redaction policy is part of the contract — a change to a field's redaction class is a contract bump.

**By construction, this schema is non-score-moving:**

- v1.0 fields are evidence; they do not feed scoring math directly.
- The link from this schema to scoring runs through the rescore feed (BEAAA-5818) and calibration feed (BEAAA-5818), which today operate as **inputs to Actuary methodology**, not as automatic mutations of `pricing/rates.yaml`.
- Any field added in v1.x.y that becomes an input to the rescore or calibration feed math is a **rule-change** under supervision-v1.1 and requires UW + Actuary + Founder co-sign before merge.

**Carrier Rule** (per `house-style.md`): the schema records evidence that helps carriers price renewals and pay claims correctly. The schema is **not** a mechanism for retroactive claim denial. Wording on the claims-queue sink and on customer-facing artifacts (BEAAA-5817 / BEAAA-5819) carries this constant; the schema itself is policy-neutral.

---

## §Field-shape conventions

Every event in this schema is a JSON object. Field types follow these conventions:

- `string` — UTF-8 string.
- `uuid` — RFC 4122 v4 string form `xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx`.
- `sha256` — string `sha256:<64 lowercase hex chars>` (matches `are-scanner-api-v1@v1.0` §Canonical-JSON serialization convention).
- `iso8601` — RFC 3339 UTC timestamp with `Z` suffix, second precision or finer.
- `int` — JSON number with no fractional part.
- `float` — JSON number, RFC 8785 JCS-canonicalizable.
- `bool` — JSON `true` / `false`.
- `enum(...)` — finite string set; values listed inline.
- `array<T>` — JSON array of elements of type `T`.
- `object` — sub-object (its own field table).
- Sentinel: `null` is **not** a valid value for any field declared below. Absent fields use **omission** (no key), not `null`.

Canonical-JSON serialization for SHA derivation follows `are-scanner-api-v1@v1.0` §Canonical-JSON serialization (RFC 8785 JCS).

---

## §Redaction policy classes (four canonical classes)

Every field declares one of:

| Class | Meaning | Normalizer behavior on receipt |
|---|---|---|
| `clear` | Field is safe to retain in cleartext. Used for IDs, enums, counts, timestamps, structural metadata. | Pass through as-is. |
| `hash` | Field is retained as `sha256:` of the canonical-JSON byte string of the original value. Original value is **not** retained. | Compute `sha256:<hex>` from the canonical-JSON form, store the digest, discard the original. |
| `redact-on-receipt` | Original value passes through a deterministic redaction pass that removes PII and secret-family patterns; the redacted text is retained. | Apply `[REDACTED:<class>]` substitution per redaction profile; retain redacted text. |
| `drop` | Field is dropped at the normalizer. Original value is **not** retained in any form (no hash, no preview). | Strip the field from the typed event entirely; record its presence in `redaction_applied`. |

**Floor rule** (per ADR-0034 §2.3): regardless of declared class, every string-typed field is run through the connector secret-family detector (`_REDACTION_PATTERNS` from BEAAA-5740). Any match is replaced with `[REDACTED:secret_family]` **before** the declared class is applied. The secret-family floor cannot be relaxed below `redact-on-receipt` by any field declaration. Drift in `_REDACTION_PATTERNS` is itself a contract bump.

**`redaction_applied`**: every event carries a `redaction_applied` array (declared in §Common envelope) that lists the field-paths the normalizer altered, in deterministic order. Empty array = no field altered.

---

## §Provenance classes (four canonical classes)

Every field declares its **provenance** — who emits the field on the wire:

| Class | Meaning |
|---|---|
| `customer_agent` | Emitted by the customer's agent runtime (the system under coverage). Carried from agent log line through the adapter to the normalizer. |
| `customer_log_sink` | Added or transformed by the customer's log sink (Datadog / Splunk / CloudWatch / OTEL / raw stdout) before reaching BackBond. Includes vendor-shape envelope metadata. |
| `backbond_normalizer` | Computed by BackBond's normalizer. Deterministic from inputs; replayable from raw object + schema version. |
| `backbond_detector` | Set by the BackBond peril-detector framework after normalization. Only present on `claim_candidate_event`. |

Provenance is a contract-layer commitment: the normalizer rejects events whose carried provenance disagrees with the declared provenance for that field.

---

## §Common envelope (every event carries these)

| Field | Type | Required | Redaction | Provenance | Notes |
|---|---|---|---|---|---|
| `schema_version` | `enum("log-event-schema-v1.0")` | yes | `clear` | `backbond_normalizer` | Byte-locked at v1.0. Bumps follow §Change control. |
| `event_type` | `enum(agent_invocation, llm_completion, tool_call, tool_source_reference, escalation, error, security_event, pii_emission, claim_candidate_event)` | yes | `clear` | `backbond_normalizer` | Drives downstream type dispatch. |
| `event_id` | `uuid` | yes | `clear` | `backbond_normalizer` | New per event. |
| `customer_id` | `uuid` | yes | `hash` on portfolio-export surfaces; `clear` on internal sinks | `backbond_normalizer` | Two-form treatment is enforced at sink boundary, declared `hash` here as the **export-surface** default. |
| `agent_id` | `uuid` | yes | `hash` on portfolio-export surfaces; `clear` on internal sinks | `backbond_normalizer` | Same two-form treatment. |
| `session_id` | `uuid` | yes | `hash` | `customer_agent` | Stable for the lifetime of one agent session. Hashed on retention; cleartext only inside the normalizer's process memory. |
| `turn_index` | `int` | conditional — required when `event_type ∈ {llm_completion, tool_call, tool_source_reference, escalation}`; omitted for `agent_invocation`, `error`, `security_event`, `pii_emission`, `claim_candidate_event` | `clear` | `customer_agent` | Zero-indexed within a session. |
| `t_event` | `iso8601` | yes | `clear` | `customer_agent` | Customer agent's clock at emission. |
| `t_received` | `iso8601` | yes | `clear` | `backbond_normalizer` | BackBond normalizer's clock at parse. Monotonic per-customer per-day. |
| `source_kind` | `enum(datadog, splunk, cloudwatch, otel, stdout, other)` | yes | `clear` | `customer_log_sink` | Adapter that produced the raw record. |
| `manifest_sha` | `sha256` | yes | `clear` | `backbond_normalizer` | SHA-256 of the signed daily manifest the event came from (per ADR-0034 §2.1). Provides cold-storage traceback. |
| `redaction_applied` | `array<string>` | yes | `clear` | `backbond_normalizer` | Field-paths the normalizer altered. Empty array allowed. |
| `partial` | `bool` | yes | `clear` | `backbond_normalizer` | `true` ⇒ source record was incomplete or partially parsed; downstream sinks MUST treat the event as evidence-only, not as a complete observation. |

Wire-shape gate: every common-envelope field is **always present** on every accepted event. A normalizer that emits an event without one of the above fields is in violation of v1.0.

---

## §Event family — `agent_invocation`

Marks the start of an agent session under coverage. Replaces / formalizes the ADR-0034 §3.1 `agent_session_start` sketch.

| Field | Type | Required | Redaction | Provenance | Notes |
|---|---|---|---|---|---|
| `agent_version` | `string` | yes | `clear` | `customer_agent` | Customer-asserted agent version string. |
| `agent_invocation_args_preview` | `string` | optional | `redact-on-receipt` | `customer_agent` | The agent's invocation arguments / system prompt opening. Redaction-on-receipt strips PII and secret-family patterns. |
| `agent_invocation_args_sha` | `sha256` | yes | `clear` | `backbond_normalizer` | SHA over the raw (pre-redaction) args canonical-JSON. Stable across redaction-pattern drift on the preview field. |
| `model` | `string` | optional | `clear` | `customer_agent` | LLM model identifier at session start, if known. |
| `scanner_version_at_bind` | `string` | yes | `clear` | `backbond_normalizer` | The `are-scanner-api-v1@vX.Y.Z` version active when this customer's policy was bound. Anchors observed-vs-predicted comparisons. |
| `entrypoint` | `string` | optional | `clear` | `customer_agent` | Free-text label (e.g. `chat`, `cron`, `webhook_inbound`). |

---

## §Event family — `llm_completion`

One LLM call within a session.

| Field | Type | Required | Redaction | Provenance | Notes |
|---|---|---|---|---|---|
| `prompt_sha` | `sha256` | yes | `clear` | `backbond_normalizer` | SHA over the customer-side raw prompt (pre-redaction). |
| `prompt_preview` | `string` | optional | `redact-on-receipt` | `customer_agent` | Truncated, redacted prompt for forensic visibility. Customer policy MAY set this to `drop` at the adapter; if dropped, omit. |
| `completion_sha` | `sha256` | yes | `clear` | `backbond_normalizer` | SHA over the raw completion. |
| `completion_preview` | `string` | optional | `redact-on-receipt` | `customer_agent` | Same posture as `prompt_preview`. |
| `model` | `string` | yes | `clear` | `customer_agent` | Model identifier for this call (may differ from session start). |
| `tokens_in` | `int` | yes | `clear` | `customer_log_sink` | Prompt token count. |
| `tokens_out` | `int` | yes | `clear` | `customer_log_sink` | Completion token count. |
| `latency_ms` | `int` | yes | `clear` | `customer_log_sink` | Wall-clock latency. |
| `refusal_class` | `enum(none, soft_refusal, hard_refusal, off_topic, unparseable)` | yes | `clear` | `backbond_normalizer` | Output of the BEAAA-5757 `RefusalClassifier` substrate, lifted into the normalizer. Deterministic from `completion_sha` preimage at receipt. |
| `completion_pii_count` | `int` | yes | `clear` | `backbond_normalizer` | Count of PII-class detections inside the raw completion (before redaction). Drives the `pii_emission` event when nonzero. |
| `cost_usd` | `float` | optional | `clear` | `customer_log_sink` | Customer-asserted call cost. Used for P6 (operational anomaly) baselining when activated. |

---

## §Event family — `tool_call`

One tool invocation within a session.

| Field | Type | Required | Redaction | Provenance | Notes |
|---|---|---|---|---|---|
| `tool_name` | `string` | yes | `clear` | `customer_agent` | Tool identifier on the customer side. |
| `tool_source_kind` | `enum(internal, http, db, fs, mcp, plugin, other)` | yes | `clear` | `customer_agent` | Tool source family. Mirrors the pre-bind `MockToolSource` shape (BEAAA-5752 helpers). |
| `tool_scope` | `string` | yes | `clear` | `customer_agent` | Tool scope label (e.g. `read_email`, `send_payment`). Drives P2 (tool misuse) scope-escape detection. |
| `args_sha` | `sha256` | yes | `clear` | `backbond_normalizer` | SHA over raw args. |
| `args_preview` | `string` | optional | `redact-on-receipt` | `customer_agent` | Same posture as `prompt_preview`. |
| `return_sha` | `sha256` | yes | `clear` | `backbond_normalizer` | SHA over raw return. |
| `return_preview` | `string` | optional | `redact-on-receipt` | `customer_agent` | Same posture as `prompt_preview`. |
| `ok` | `bool` | yes | `clear` | `customer_agent` | Tool reported success. |
| `latency_ms` | `int` | yes | `clear` | `customer_log_sink` | Wall-clock latency. |
| `tool_source_reference_event_id` | `uuid` | optional | `clear` | `backbond_normalizer` | If the tool call resolves to an external content source, link to the paired `tool_source_reference` event. |

---

## §Event family — `tool_source_reference`

Records that a tool returned content from an external source. Drives P3.2 (indirect injection) and P4.2 (hallucinated citation) detectors.

| Field | Type | Required | Redaction | Provenance | Notes |
|---|---|---|---|---|---|
| `source_kind` | `enum(http_url, http_api, db_row, fs_file, mcp_resource, other)` | yes | `clear` | `customer_agent` | Source family. |
| `source_id_sha` | `sha256` | yes | `clear` | `backbond_normalizer` | SHA over the source identifier (URL, file path, row PK). The cleartext identifier is `drop`. |
| `content_sha` | `sha256` | yes | `clear` | `backbond_normalizer` | SHA over the source content (post-fetch, pre-LLM-ingest). |
| `content_preview` | `string` | optional | `redact-on-receipt` | `customer_agent` | Truncated, redacted preview. |
| `paired_tool_call_event_id` | `uuid` | yes | `clear` | `backbond_normalizer` | Back-link to the `tool_call` event that surfaced this source. |

---

## §Event family — `escalation`

The agent escalated to a human (UI surface, oncall page, support ticket, refusal-and-stop, etc.).

| Field | Type | Required | Redaction | Provenance | Notes |
|---|---|---|---|---|---|
| `reason_code` | `enum(unable_to_complete, refusal_policy, high_uncertainty, customer_escape_hatch, security_policy_block, other)` | yes | `clear` | `customer_agent` | Reason taxonomy. |
| `transcript_sha` | `sha256` | yes | `clear` | `backbond_normalizer` | SHA over the rolling session transcript at point of escalation. |
| `severity` | `enum(low, medium, high, critical)` | yes | `clear` | `customer_agent` | Customer-asserted severity. |
| `notified_human` | `bool` | yes | `clear` | `customer_agent` | `true` ⇒ a human was actually paged / contacted (not just queued). |
| `notification_channel` | `enum(ui, page, ticket, email, slack, other, none)` | yes | `clear` | `customer_agent` | Notification surface. `none` allowed when `notified_human=false`. |

---

## §Event family — `error`

An error within a session that did not (necessarily) cause escalation.

| Field | Type | Required | Redaction | Provenance | Notes |
|---|---|---|---|---|---|
| `code` | `string` | yes | `clear` | `customer_agent` | Customer-side error code label. |
| `message_preview` | `string` | optional | `redact-on-receipt` | `customer_agent` | Truncated, redacted message. |
| `message_sha` | `sha256` | yes | `clear` | `backbond_normalizer` | SHA over raw message. |
| `recoverable` | `bool` | yes | `clear` | `customer_agent` | Customer-asserted recoverability. |
| `retry_count` | `int` | yes | `clear` | `customer_agent` | Retries already attempted at point of emission. |

---

## §Event family — `security_event`

A security-relevant pattern was observed in the input, the source content, the completion, or the ingest pipeline itself.

| Field | Type | Required | Redaction | Provenance | Notes |
|---|---|---|---|---|---|
| `pattern_family` | `enum(secret_family, prompt_injection_marker, scope_escape_marker, runaway_loop_marker, signature_failure, kms_decrypt_failure, redaction_floor_triggered, other)` | yes | `clear` | `backbond_normalizer` | Pattern family. Hooks the BEAAA-5740 floor and the BEAAA-5757 P5 markers. |
| `severity` | `enum(low, medium, high, critical)` | yes | `clear` | `backbond_normalizer` | |
| `evidence_event_id` | `uuid` | yes | `clear` | `backbond_normalizer` | Pointer to the `llm_completion`, `tool_call`, `tool_source_reference`, or `error` event that triggered detection. |
| `evidence_sha` | `sha256` | yes | `clear` | `backbond_normalizer` | SHA over the specific snippet matched. |
| `detector_id` | `string` | yes | `clear` | `backbond_normalizer` | Detector module + version (e.g. `secret_family@5740.1`). Note: this is the **pattern-level** detector, not a peril detector — peril detector verdicts flow through `claim_candidate_event`. |

---

## §Event family — `pii_emission`

A PII-class value was observed in agent output before redaction.

| Field | Type | Required | Redaction | Provenance | Notes |
|---|---|---|---|---|---|
| `pii_class` | `enum(email, phone, ssn, credit_card, bank_account, address, name, dob, government_id, other_pii)` | yes | `clear` | `backbond_normalizer` | PII family. |
| `count` | `int` | yes | `clear` | `backbond_normalizer` | Number of distinct PII items detected. |
| `location` | `enum(prompt, completion, tool_args, tool_return, tool_source_content, error_message)` | yes | `clear` | `backbond_normalizer` | Where the PII appeared. |
| `evidence_event_id` | `uuid` | yes | `clear` | `backbond_normalizer` | Source event. |
| `redaction_method_used` | `enum(redact-on-receipt, hash, drop, none)` | yes | `clear` | `backbond_normalizer` | What the normalizer did with the field carrying the PII. `none` ⇒ the source-field policy was `clear` and the normalizer floor did NOT match — this is a violation and MUST also fire a `security_event(redaction_floor_triggered)`. |

---

## §Event family — `claim_candidate_event`

Output of the peril-detector framework (per ADR-0034 §4). The **only** event family whose `provenance` is `backbond_detector`. Consumed by the claims-queue sink (BEAAA-5817).

| Field | Type | Required | Redaction | Provenance | Notes |
|---|---|---|---|---|---|
| `peril` | `enum(P1, P2, P3, P4, P5, P6)` | yes | `clear` | `backbond_detector` | Peril taxonomy from rubric. |
| `detector_id` | `string` | yes | `clear` | `backbond_detector` | E.g. `P4.numeric_claim_tool_mismatch`. |
| `detector_version` | `string` | yes | `clear` | `backbond_detector` | Version of the detector module that fired. |
| `verdict_status` | `enum(pass, fail, error)` | yes | `clear` | `backbond_detector` | Mirrors `ProbeVerdict.status` from BEAAA-5754. |
| `verdict_reason` | `string` | yes | `clear` | `backbond_detector` | Taxonomy mirrors `ProbeVerdict.reason`. |
| `severity` | `enum(low, medium, high, critical)` | yes | `clear` | `backbond_detector` | Per-peril severity from detector. |
| `evidence_event_refs` | `array<uuid>` | yes | `clear` | `backbond_detector` | Event IDs (1+ entries) that constitute the evidence trail. |
| `transcript_sha` | `sha256` | yes | `clear` | `backbond_detector` | SHA over the relevant transcript span. |
| `seed` | `int` | conditional — required when the detector is non-deterministic across input ordering | `clear` | `backbond_detector` | Reproducibility per Supreme Law. |

`claim_candidate_event` is **evidence-only on v1.0** (per §Scope and posture). The claims-queue sink emitter (BEAAA-5817 child on firewall lift) reads these events and routes to the UW review queue; **no field on this event mutates `are_total` or `pricing/rates.yaml` in v1.0**.

---

## §Cross-event invariants

The normalizer MUST guarantee:

1. **Session continuity.** Every `llm_completion`, `tool_call`, `tool_source_reference`, `escalation`, `error`, `pii_emission`, `security_event` event for a session is preceded (in `t_event` order) by exactly one `agent_invocation` event for the same `session_id`. Detected violations emit `security_event(pattern_family="other", detector_id="schema.session_continuity")`.
2. **Turn monotonicity.** Within a `session_id`, `turn_index` is non-decreasing across events that declare `turn_index`. Detected violations emit the same diagnostic.
3. **Reference resolution.** Every `tool_source_reference.paired_tool_call_event_id` MUST resolve to a `tool_call` event in the same session. Unresolved references emit the diagnostic.
4. **Claim evidence integrity.** Every `claim_candidate_event.evidence_event_refs[]` entry MUST resolve to a non-`claim_candidate_event` event in the same session, observed at or before the claim candidate's `t_event`. Unresolved evidence makes the claim candidate **fail-closed** (sink emitter discards the event and emits a `security_event`).
5. **Schema-version stability.** All events in a single signed daily manifest carry the same `schema_version`. Mixed-version manifests are rejected at ingest.

These invariants are not optional — they are part of the contract and the normalizer reference implementation MUST enforce them.

---

## §Change control

Same regime as `are-scanner-api-v1@v1.0`. Reproduced here for completeness:

| Change class | Examples | Co-sign required | Notes |
|---|---|---|---|
| **Patch (v1.0.x)** | Documentation clarification; non-normative example update; typo fix; metadata correction not changing semantics | **CTO + Scanner Engineer** | No field add/remove/rename; no redaction class change; no provenance change. |
| **Minor (v1.x.y), non-score-feeding** | Add a new optional field to an existing event family; add a new event family; tighten an enum (add a member); add a new redaction class to the canonical four (rare, would need ADR) | **CTO + Scanner Engineer**; **UW courtesy** | Pure additive; old consumers continue to work byte-stably. |
| **Minor (v1.x.y), score-feeding** | Add a field that the rescore feed (BEAAA-5818) or calibration feed (BEAAA-5818) will read into scoring math | **CTO + Scanner Engineer + UW + Actuary + Founder** | Rule-change under supervision-v1.1. N=5 calibration delta required on golden agents per CTO Supreme Law. |
| **Major (v2.0)** | Rename or remove a field; loosen an enum (remove a member); relax a redaction class on an existing field; change a provenance class on an existing field | **CTO + Scanner Engineer + UW + Founder** | Breaking. Consumers MUST recompile / re-pin. v1.0 consumers continue to work against the v1 normalizer in a backward-compatibility window declared at v2.0 publish. |
| **Activation of a stub detector** | Activate P1 / P2 / P3 / P6 such that `claim_candidate_event` for that peril is emitted into the rescore feed | **CTO + Scanner Engineer + UW + Actuary + Founder** | Rule-change under supervision-v1.1; classification per ADR-0034 §7 |

**Floor non-relaxation.** §Redaction policy classes specifies that the connector secret-family floor cannot be relaxed below `redact-on-receipt` by any field declaration. Any proposed change that would relax this floor is **rule-change + counsel** and is escalated to Founders before any contract bump.

**Reproducibility.** Per Supreme Law (`cto-os.md`), every score derivable from this schema's events is reproducible to ±1 point on the total ARE and ±1 point on every subscore, across N=5 runs against the golden set. The reproducibility test fixture for v1.0 lands with the normalizer implementation child (Phase 2 firewall-lift).

---

## §Co-sign block

| Role | Required for v1.0 | Required for v1.x.y additive | Required for v1.x.y score-feeding | Required for v2.0 |
|---|---|---|---|---|
| CTO (contract owner) | yes | yes | yes | yes |
| Scanner Engineer (field-semantics co-author) | yes | yes | yes | yes |
| Underwriter | courtesy | courtesy | **yes** | **yes** |
| Actuary | n/a | n/a | **yes** | n/a |
| CEO | n/a | n/a | n/a | n/a |
| Founder | n/a | n/a | **yes** | **yes** |

**v1.0 publish path** (this revision):
- CTO co-sign comment on BEAAA-5816 with this revision id.
- Scanner Engineer co-sign comment on BEAAA-5816 with this revision id.

Until both are in place, the schema is **draft**. Once both are in place, v1.0 is **byte-locked** and any further change follows the table above.

---

## §Non-Output guarantee

This schema is **outside** `are-scanner-api-v1@v1.0` Output. None of its fields appear in the v1.x.y scan / rescan response payloads. The pre-bind `are_total`, pillar subscores, band, confidence, `pricing/rates.yaml` rate book, and rubric weights remain byte-stable. The observed-vs-predicted appendix (BEAAA-5760/5761 rollup extension under ADR-0034 §6) reads **aggregated detector verdicts derived from this schema's events**, not the raw events — and the appendix itself is pure export, not a contract Output bump. **Δ=0 by construction** on `are-scanner-api-v1@v1.0`.

---

## §Self-audit (Supreme Law gate)

| Check | Result |
|---|---|
| `are-scanner-api-v1@v1.0` Output byte-stable | yes (no v1 field touched by this schema) |
| Pillar weights unchanged | yes |
| `pricing/rates.yaml` untouched | yes |
| Rubric weights unchanged | yes |
| `score_invariants` block stability (BEAAA-5761) | not applicable — this is a contract document, not a scoring code change |
| N=5 calibration delta on golden set | not applicable on v1.0 publish — no scoring path touched; required at first normalizer / detector implementation child (post-firewall) |
| Carrier Rule wording | applied to §Scope and posture and to the consumer notes on `claim_candidate_event` |
| Tamper resistance | preserved: every byte-stable field uses `sha256:` digests; cleartext content is `redact-on-receipt` or `drop` per posture C |
| Privacy posture | C — hybrid schema-driven, declared per-field, enforced by normalizer floor |
| Metadata block per `artifact-metadata-schema.md` | present in frontmatter; required keys populated |

`self_audit_passed: true` is set in the frontmatter.
