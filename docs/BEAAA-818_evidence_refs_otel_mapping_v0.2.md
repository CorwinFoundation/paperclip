---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "v0.2 OTel GenAI semconv mapping for ARE evidence_refs span attributes — formal alignment to opentelemetry-semantic-conventions@1.26.0 GenAI incubating conventions, with vendor-namespaced ifa.are.* extensions for ARE-specific fields."
contracts_referenced: ["are-scanner-api-v1@v1.0.4"]
ticket_refs: ["BEAAA-818", "BEAAA-808", "BEAAA-343", "BEAAA-767"]
version: "v0.2"
prior_version: "BEAAA-343_evidence_refs_otel_mapping_v0.1"
semconv_version_pinned: "opentelemetry-semantic-conventions@1.26.0"
self_audit_passed: true
---

# ARE Evidence Refs — OTel GenAI Semconv Mapping v0.2

**Origin:** BEAAA-818, child of BEAAA-808 memo `oss-leverage-scan` rev 1.
**Semconv pin:** `opentelemetry-semantic-conventions@1.26.0` (GenAI conventions, incubating).
**Change from v0.1:** Formal alignment pass; vendor namespace normalised to `ifa.are.*`; two-span-layer model made explicit; delta section for ARE-specific fields without OTel coverage.

---

## 1. Span model

ARE produces **two layers of OTel spans**. Keep them distinct.

### Layer 1 — Scan span (one per `are-scan` invocation)

The root span for the entire scan. Carries the score envelope as span attributes.

```
span name: "are.scan"
span kind: INTERNAL
```

### Layer 2 — Probe child spans (one per LLM judge call)

Child spans representing individual LLM invocations made by the scanner's judge and detector models. These are where OTel GenAI semconv applies **directly and natively**.

```
span name: "gen_ai.{operation.name}"   # e.g. "gen_ai.chat"
span kind: CLIENT
parent: the are.scan span
```

Customers piping ARE spans into Datadog / Honeycomb / Phoenix / Langfuse see:
- Layer 1 for the score summary
- Layer 2 for the individual LLM call traces with token costs, latency, finish reasons

---

## 2. Layer 1 — Scan span attribute mapping

### 2a. Fields with direct OTel GenAI semconv mappings

| ARE field (contract v1.0.3) | OTel attribute name | Semconv ref | Notes |
|---|---|---|---|
| `model_versions.primary_judge` | `gen_ai.request.model` | GenAI §request | The primary judge model ID used for scoring. If multiple judge families, emit the primary; see §2c for the full map. |
| `run_timestamp` | — | (use OTel span start timestamp) | Do not duplicate as an attribute; the span start time IS the run timestamp. |

> **Note:** `gen_ai.system` is intentionally omitted at Layer 1. The ARE Scanner is not itself an LLM; the `gen_ai.system` attribute describes the AI system being called in a Layer 2 probe span. Emitting `gen_ai.system = "ifa.are"` on the root scan span would misrepresent the OTel model.

### 2b. ARE-specific fields — vendor namespace `ifa.are.*`

All ARE fields without a direct OTel GenAI equivalent use the `ifa.are.` vendor prefix per OTel conventions for vendor extensions.

**Score envelope:**

| ARE field | OTel attribute name | Type | Notes |
|---|---|---|---|
| `are_total` | `ifa.are.ari_total` | int [0..100] | Canonical score per contract v1.0.3. |
| `subscores.GOVERNANCE` | `ifa.are.subscore.governance` | int [0..25] | |
| `subscores.ROBUSTNESS` | `ifa.are.subscore.robustness` | int [0..25] | |
| `subscores.BIAS_FAIRNESS` | `ifa.are.subscore.bias_fairness` | int [0..25] | |
| `subscores.OPS` | `ifa.are.subscore.ops` | int [0..25] | |
| `band` | `ifa.are.band` | string | e.g. "B1", "B2", "B3", "B4" |
| `confidence` | `ifa.are.confidence` | float [0..1] | MUST emit per v1.0.3. |
| `verdict.bind_eligibility` | `ifa.are.verdict.bind_eligibility` | string enum | `eligible \| ineligible_attestation \| ineligible_substrate \| review_required` |

**Provenance / reproducibility fields (MUST be present per Hard Guarantee #3):**

| ARE field | OTel attribute name | Type | Notes |
|---|---|---|---|
| `scanner_version` | `ifa.are.scanner_version` | string | e.g. "0.3.2" |
| `corpus_version` | `ifa.are.corpus_version` | string | e.g. "corpus-v1.2.0" |
| `seed` | `ifa.are.seed` | int | Fixed per run. |

**Coverage fields:**

| ARE field | OTel attribute name | Type | Notes |
|---|---|---|---|
| `uncovered_dimensions[]` | `ifa.are.uncovered_dimensions` | string[] | Array of dim IDs; MUST emit per v1.0.3. |

**Model versions (full map):**

| ARE field | OTel attribute name | Type | Notes |
|---|---|---|---|
| `model_versions` (full dict) | `ifa.are.model_versions` | string (JSON-encoded) | Full map, JSON-encoded string. Collectors that parse nested JSON may expand to `ifa.are.model_versions.{key}`. |
| `model_versions.detectors[]` | `ifa.are.model_versions.detectors` | string[] | Detector model IDs as array. |

### 2c. evidence_refs[] — as span events

Each `evidence_refs[]` entry is emitted as a **span event** on the Layer 1 scan span (not a child span). Event name: `"are.evidence"`.

| evidence_refs field | Span event attribute name | Type | Notes |
|---|---|---|---|
| `rubric_point_id` | `ifa.are.rubric_point_id` | string | e.g. "P1.1", "GOV.1" |
| `test_id` | `ifa.are.test_id` | string | |
| `artifact_uri` | `ifa.are.artifact_uri` | string | May be synthetic (e.g. `"attestation:P4.1"`) |
| `verdict` | `ifa.are.evidence.verdict` | string enum | `pass \| partial \| fail \| n/a` — renamed from bare `verdict` to avoid collision with top-level `ifa.are.verdict.bind_eligibility` |
| `weight` | `ifa.are.evidence.weight` | float | Normalised contribution [0..1]. MUST emit per v1.0.3. |
| `review_state` (P4 only) | `ifa.are.evidence.review_state` | string enum | `unreviewed \| uw_reviewed \| uw_rejected`. MUST emit on P4 entries per v1.0.3. |
| `tier` (P4 only) | `ifa.are.evidence.tier` | int [1..4] | MUST emit on P4 entries per v1.0.3. |
| `na_justification` | `ifa.are.evidence.na_justification` | string | Present only when `verdict = "n/a"`. |

---

## 3. Layer 2 — Probe child span attribute mapping

Layer 2 spans are generated by the ARE scanner's LLM judge and detector model invocations. These use OTel GenAI semconv **directly**.

| OTel attribute | Value source | Notes |
|---|---|---|
| `gen_ai.system` | model provider (e.g. `"anthropic"`, `"openai"`) | Derived from `model_versions.primary_judge` prefix. |
| `gen_ai.operation.name` | `"chat"` | All ARE judge calls are chat completions. |
| `gen_ai.request.model` | the specific model ID called | e.g. `"claude-haiku-4-5-20251001"` |
| `gen_ai.response.model` | from LLM response header if available | May equal `gen_ai.request.model`. |
| `gen_ai.usage.input_tokens` | from LLM response usage block | |
| `gen_ai.usage.output_tokens` | from LLM response usage block | |
| `gen_ai.response.finish_reason[]` | from LLM response | Array; typically `["stop"]`. |
| `ifa.are.probe_id` | the probe/dimension ID being evaluated | ARE-specific; links child span back to `rubric_point_id`. |

---

## 4. Delta: ARE-specific fields with no OTel GenAI semconv equivalent

The following ARE fields have **no matching OTel GenAI semconv attribute** as of `opentelemetry-semantic-conventions@1.26.0`. All use `ifa.are.*` vendor namespace per OTel conventions. These are tracked as candidate contributions to the OTel GenAI semconv upstream if the conventions mature to cover evaluation/scoring use cases.

| ARE concept | Vendor attribute | Rationale for no OTel equivalent |
|---|---|---|
| Rubric point identifier | `ifa.are.rubric_point_id` | Evaluation rubrics are domain-specific; OTel GenAI semconv covers LLM call attributes, not rubric taxonomy. |
| Risk score / ARI | `ifa.are.ari_total`, `ifa.are.subscore.*` | Composite risk scores are product-specific; no generic "ai_risk_score" in semconv. |
| Corpus version | `ifa.are.corpus_version` | Eval corpus provenance; no equivalent in LLM call semconv. |
| Seed | `ifa.are.seed` | Reproducibility seed; no equivalent. |
| Confidence | `ifa.are.confidence` | Scanner-level confidence interval; no equivalent. |
| Coverage / uncovered dims | `ifa.are.uncovered_dimensions` | Coverage accounting; no equivalent. |
| Bind eligibility verdict | `ifa.are.verdict.bind_eligibility` | Insurance/underwriting concept; far outside GenAI semconv scope. |
| Evidence verdict per dim | `ifa.are.evidence.verdict` | Per-rubric-point pass/fail; no equivalent. |
| Evidence weight | `ifa.are.evidence.weight` | Weighted scoring; no equivalent. |
| P4 review_state | `ifa.are.evidence.review_state` | UW review state; insurance-domain-specific. |
| P4 tier | `ifa.are.evidence.tier` | Attestation tier; insurance-domain-specific. |
| Detector model versions | `ifa.are.model_versions.detectors` | Multi-model ensemble versioning; only single-model `gen_ai.request.model` in semconv. |

---

## 5. Transition window and back-compat shim

The back-compat shim (`harness/otel_shim.py`) translates the existing v1.0.3 Python dict field names to OTel attribute names for span export. The shim is:

- **Additive only:** existing dict keys are preserved; OTel attribute names are added alongside.
- **Opt-in at span export time:** the shim is invoked in the OpenTelemetry exporter path, not in the scoring pipeline. The scoring pipeline and Underwriter rating engine continue to read existing field names.
- **Transition window:** `are-scanner-api-v1@v1.0.3` field names remain the canonical interchange format. `v1.0.4` adds the OTel attribute names as the customer-facing telemetry surface. Old names are not removed in v1.0.4.

Removal of legacy names (if ever) requires a fresh CTO + Underwriter co-sign on a v2.0 contract ticket.

---

## 6. Semconv version management

| Pinned version | `opentelemetry-semantic-conventions@1.26.0` |
|---|---|
| Status | GenAI conventions: incubating |
| Upstream churn policy | Pin is locked per this document. Any upstream semconv change that would alter an attribute name listed in §2 or §3 requires a BEAAA ticket and CTO review before adopting. |
| Next planned review | At OTel GenAI semconv GA (stable) or at scanner v0.4 planning, whichever comes first. |

---

## 7. Collector compatibility notes

Customers piping ARE spans into the following collectors receive the named attributes without transform:

| Collector | Layer 1 (ifa.are.*) | Layer 2 (gen_ai.*) |
|---|---|---|
| Datadog APM | Custom attributes (auto-indexed if listed in DD facets) | Native GenAI semconv support in DD Agent 7.50+ |
| Honeycomb | All string/int/float attributes visible natively | Native support |
| Arize Phoenix | Custom columns; no native ifa.are.* schema | Native gen_ai.* support |
| Langfuse | Score via OTLP ingest; custom fields pass through | Native gen_ai.* support |
| OpenTelemetry Collector (generic) | Pass-through; no schema enforcement | Pass-through |

No vendor-specific transform is required. ARE spans are self-describing via this mapping document.
