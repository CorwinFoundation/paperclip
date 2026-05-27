---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "v1.0.4 contract patch — naming-only: adopt OTel GenAI semconv attribute names for ARE span telemetry surface; back-compat shim preserves existing v1.0.3 field names for Underwriter and rating engine; no scoring logic, rubric, or envelope schema change."
contracts_referenced: ["are-scanner-api-v1@v1.0.3 → v1.0.4"]
ticket_refs: ["BEAAA-818", "BEAAA-808"]
proof_points_cited: ["OTel GenAI semconv pin opentelemetry-semantic-conventions@1.26.0", "BEAAA-818 mapping doc v0.2 (docs/BEAAA-818_evidence_refs_otel_mapping_v0.2.md)", "BEAAA-818 back-compat shim (harness/otel_shim.py)", "CTO sign-off BEAAA-818"]
version: "v1.0.4-patch"
prior_version: "are-scanner-api-v1@v1.0.3"
self_audit_passed: true
escalations_noted:
  - "Change classification: naming-only, additive telemetry surface. Scoring math unchanged. Rubric unchanged. Pillar weights unchanged (25/25/25/25). Envelope schema unchanged (no new fields in the ARE output dict). Underwriter rating engine reads v1.0.3 field names; those are preserved. No golden score delta expected (shim is not in scoring path). No HoUW watchpoint triggered. No Founder gate triggered."
  - "Underwriter informational notice required (not co-sign-gated per issue scope: naming-only). Notice: ARE now exports OTel-compatible span attributes via otel_shim.py; Underwriter rating engine is unaffected (reads v1.0.3 names from the existing envelope dict, not from the OTel exporter path)."
---

# Contract patch — `are-scanner-api-v1.md` v1.0.3 → v1.0.4

**Status:** CTO-signed. Informational notice to Underwriter required before customer-facing OTel export is enabled; Underwriter co-sign NOT required (naming-only per BEAAA-818 issue scope).
**Change classification:** Additive telemetry surface (new OTel span attribute names alongside existing dict field names). No envelope schema change. No scoring math change.
**Rollback:** Tier 1, ≤2 min. Disable the OTel exporter / otel_shim.py call site. No data migration. Score output unaffected.

---

## §New section: Telemetry surface (OTel GenAI semconv)

Add the following section to the contract:

### OTel span export (v1.0.4)

ARE v0.3.3+ emits OpenTelemetry spans compatible with any OTel-compliant collector. Attribute naming follows `opentelemetry-semantic-conventions@1.26.0` GenAI conventions (incubating) where applicable; ARE-specific fields use the `ifa.are.*` vendor namespace per OTel extension conventions.

**Semconv pin:** `opentelemetry-semantic-conventions@1.26.0`. Upstream churn requires a BEAAA ticket and CTO review before adopting.

**Two-layer span model:**

1. **`are.scan` span (INTERNAL):** root span per scan invocation. Carries score envelope as span attributes using `ifa.are.*` names. Each `evidence_refs[]` entry is a span event (`are.evidence`).
2. **`gen_ai.chat` child spans (CLIENT):** one per LLM judge/detector call. Use native OTel GenAI semconv attributes (`gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.response.finish_reason[]`).

**Back-compat shim:** `harness/otel_shim.py` translates v1.0.3 dict field names to OTel attribute names at span-export time. The scoring pipeline, Underwriter rating engine, and all v1.0.3 consumers are unaffected. Legacy field names are NOT removed in v1.0.4.

**Complete attribute mapping:** `docs/BEAAA-818_evidence_refs_otel_mapping_v0.2.md`.

---

## §Versioning — bump

```
v1.0.3 → v1.0.4 (2026-05-27)
```

---

## §Version history — row appended

```
| v1.0.4 | 2026-05-27 | OTel GenAI semconv span attribute naming for ARE telemetry surface. Additive: new ifa.are.* and gen_ai.* attribute names via otel_shim.py; v1.0.3 field names preserved. No scoring math, rubric, pillar weight, envelope schema, or golden score change. Semconv pin: opentelemetry-semantic-conventions@1.26.0. CTO sign-off BEAAA-818. Underwriter informational notice. | BEAAA-818 |
```

---

## Approval gates — posture

- Pillar weight changes (Founder) — NOT triggered.
- Rubric point added/removed (CTO + UW) — NOT triggered (no rubric change).
- Scoring logic change (CTO + UW) — NOT triggered (no math change; shim is post-scoring).
- New LLM judge (CTO) — NOT triggered.
- Golden score >1pt move (CTO + UW; Founder if >3pt) — NOT triggered (shim not in scoring path; Δ = 0 expected).
- HoUW forward watchpoints (BEAAA-766) — NOT triggered (no verdict semantics change, no selection_rule change, no enum widening).
- Tamper resistance reduction (Founder) — NOT triggered (shim is read-only at export time; no new attack surface on the scoring pipeline).

## Underwriter informational notice (required, not co-sign-gated)

> **To: Underwriter**
> **Re: BEAAA-818 v1.0.4 naming change — informational only**
>
> ARE v0.3.3 adds an OTel span export surface via `harness/otel_shim.py`. The shim runs at span-export time, after the scoring pipeline completes. Your rating engine reads the v1.0.3 score envelope dict directly and is unaffected. No field names in the existing dict are changed or removed. The new OTel attribute names are additive and exist only in the span export path.
>
> No action required on your side. If you wish to consume ARE spans in your own telemetry pipeline, use the attribute names in `docs/BEAAA-818_evidence_refs_otel_mapping_v0.2.md`.

## Rollback

Tier 1, ≤2 min:
1. Remove or disable the `otel_shim.py` call site in the scan runner.
2. No score output, envelope, or calibration impact.
3. Pin scanner back to v0.3.2 if the span export entrypoint was part of the v0.3.3 release.
