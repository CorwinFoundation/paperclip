---
artifact_type: adr
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "The Monitor data contract makes the local/leaves boundary a tested invariant so production incidents become priced claims without exporting customer data."
contracts_referenced: ["are-scanner-api-v1"]
ticket_refs: ["BEAAA-7768"]
proof_points_cited: ["post-bind-ingest log-event-schema-v1.0", "detect_p5_events claim_candidate_event", "classify_red_taxonomy buckets", "redaction conformance gate"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["egress allowlist + 30-day retention proposed by Legal, pending Founder confirmation (Q2, BEAAA-7800)", "mid-term repricing disclosure DECIDED calibration-only for v0.1 (Q3, BEAAA-7794)", "log-integrity trust-for-MVP is a documented v0.1 limitation (Q5)"]
ratification_status: "draft_pending_founder"
ratification_tracker: "BEAAA-7802"
---

# Contract (DRAFT): Monitor Data Contract — v0.1

> **Status: DRAFT — pending final Founder ratification (BEAAA-7802).** Per BEAAA-7768 founder answers (2026-06-23): egress allowlist + retention require **Founder + Legal** ratification before any probe transmits (Q2 = `cto_proposes_first`); this document is that proposal. No outbound code ships until this contract is ratified.
>
> **Ratification progress (as of 2026-06-23, tracked on BEAAA-7802):**
> - **Underwriter — SIGNED (BEAAA-7794).** Q3 = `aggregates.calibration_signal` is calibration/scanner-improvement only for v0.1, **not** a Monitor-driven mid-term repricing trigger (no wording amendment carried here). Q4 = `affected_party` + `recommended_disposition` shape co-signed for claim adjudication. Redline applied: `bias` removed from per-event `claim_candidates[].peril` (see §4) — bias remains an aggregate drift signal only.
> - **Legal — PROPOSED, pending Founder confirmation (BEAAA-7800).** Egress boundary (§1/§4) approved; retention ceiling set to **30 days from receipt** for minimized claim evidence on BackBond servers; `minimal_evidence_excerpt` capped at **500 characters** per claim candidate. Guardrail: internal implementation ratification only — customer-facing terms / DPA / policy wording still route to outside counsel.
> - **Founder — PENDING.** CTO raises final board ratification of the complete package once the Legal Founder-confirmation on BEAAA-7800 lands. On ratification this banner flips DRAFT → RATIFIED.
>
> Note: the `bias` redline above is a *narrowing* of the claim-candidate schema and does **not** alter the §1 stays-local list or the §4 egress surface — Legal's egress/retention review (contract SHA `bbcd754f…`) remains valid.

**Parties:** CTO (producer) ↔ Underwriter / Claims Architect (primary consumers) ↔ Founder + Legal (ratify the egress/retention boundary).

**Relationship to `are-scanner-api-v1`:** sibling contract. The Scanner produces a pre-bind BackBond Score; the Monitor produces post-bind **claim candidates** + aggregate calibration signal from production logs. Both obey the same discipline: pinned versions, complete evidence trail, no "trust-me" outputs.

---

## 1. The boundary (the whole point)

The probe runs **inside the customer environment**. Two classes of data, one hard wall:

**STAYS LOCAL — never crosses the boundary:**
raw transcripts · full prompts / system prompts · tool inputs & outputs · API keys / tool credentials · model weights · unredacted PII · the full local evidence store that `manifest://` URIs resolve against.

**MAY LEAVE — the *only* thing the probe transmits** (the outbound artifact, §4):
minimized + redaction-conformance-gated claim candidates, aggregate statistics, and calibration/pricing signal.

**Egress invariant (release blocker):** every field in the outbound artifact is either (a) an aggregate, (b) a `manifest://` local-only pointer, or (c) a length-capped, redaction-conformance-gated minimal excerpt. A single outbound payload that fails the egress gate is a contract breach — same severity as a Scanner score shipped without an evidence trail.

## 2. Local input (stays in customer env)

Reuses the shipped schema — no new ingestion format:

```jsonc
{ "schema_version": "log-event-schema-v1.0",
  "events": [ /* agent_invocation | tool_call | tool_source_reference |
                 llm_completion | escalation | error | security_event |
                 pii_emission */ ],
  "redaction_policy": { /* path -> clear|redact-on-receipt|drop|hash */ } }
```

Ingestion is **offline** (`normalize_receiver_events` / `normalize_manifest`); no live agent access. Redaction is applied at ingest and the existing conformance gate (`RedactionConformanceError`) runs **before** any detector sees the data.

## 3. Outcome taxonomy (maps the 6 perils → claims dispositions)

Thin layer over the shipped `classify_red_taxonomy` buckets:

| Monitor outcome | Source bucket | Meaning |
|---|---|---|
| `incident` | confirmed_live_failure | Unsafe action/claim actually occurred |
| `potential_claim` | confirmed_live_failure ≥ severity/confidence threshold | Likely covered loss |
| `near_miss` | confirmed_live_failure, harm averted late | Attempt blocked at the edge |
| `safe_refusal` | safe_refusal | Good behavior — agent refused |
| `probe_artifact` | probe_family_finding | Looks like our own probe family, not production harm |
| `non_issue` | no_detection | Clean traffic |

Peril coverage at the Monitor surface: prompt injection (live) · hallucinated action (partial) · **tool misuse / runaway loop / data exfiltration (to build, WS-1)** · **bias = aggregate drift signal, not a per-conversation incident** (explicit to Underwriter).

## 4. Outbound artifact (may leave — minimized)

```jsonc
{ "claim_candidates": [ {
    "peril": "prompt_injection|tool_misuse|hallucinated_action|runaway_loop|data_exfiltration",  // bias excluded per UW redline (BEAAA-7794): bias is aggregate-only, never a per-event claim candidate
    "severity": "S0|S1|S2",
    "confidence": 0..1,
    "outcome": "incident|potential_claim|near_miss|safe_refusal|probe_artifact|non_issue",
    "affected_party": "end_user|customer|third_party|unknown",      // Q4 → reviewed by Underwriter
    "likely_harm": "financial|safety|privacy|reputational|none",
    "minimal_evidence_excerpt": "string (redacted, length-capped, egress-gated)",
    "local_evidence_ref": "manifest://...",                          // resolvable only on-site
    "dedup_group_id": "string",                                      // WS-3 grouping
    "recommended_disposition": "open_claim|monitor|dismiss_probe_artifact",  // Q4 → Underwriter adjudicates
    "detector_id": "string", "detector_version": "semver" } ],
  "aggregates": { "conversations_scanned": int, "alerts_per_1k": float,
                  "peril_histogram": {}, "calibration_signal": {} },
  "monitor_version": "semver", "corpus_version": "semver",
  "model_versions": {}, "seed": int }
```

## 5. Hard guarantees (CTO on the hook)

1. **Egress minimization** — §1 invariant, enforced by a single outbound gate + an adversarial "nothing raw escapes" test. Release blocker.
2. **Evidence completeness** — every `claim_candidate` resolves to a reachable `local_evidence_ref`. No trust-me claims (inherited from `are-scanner-api-v1`).
3. **Reproducibility** — same logs + `monitor_version` + `corpus_version` + `model_versions` + `seed` → same buckets within run-to-run noise; N=5 in CI.
4. **Redaction success** — adversarial test that no PII/secret survives into any outbound field. Release blocker, treated like Scanner tamper resistance.
5. **Dedup** — one real incident collapses to one `dedup_group_id`.

## 6. Versioning & pinning

`monitor_version`, `corpus_version`, `model_versions`, `seed` present on every outbound artifact. Any LLM judge in a detector is pinned (temp 0, fixed seed, ensemble-of-N majority) or the detector does not ship.

## 7. Open ratification items (NOT CTO-decidable — from BEAAA-7768)

- **Q2 — Egress allowlist + retention (Founder + Legal):** this contract is the proposed allowlist. **Legal proposal (BEAAA-7800): 30-day retention ceiling from receipt** on minimized claim evidence held on BackBond servers, `minimal_evidence_excerpt` capped at 500 chars. *Pending Founder confirmation* before WS-2 transmit code ships.
- **Q3 — Mid-term repricing disclosure (Underwriter): DECIDED (BEAAA-7794).** For v0.1, `aggregates.calibration_signal` is calibration / scanner-improvement only, **not** a Monitor-driven mid-term repricing trigger. No repricing path is authorized here because no policy-wording amendment is carried here; Monitor telemetry may open underwriting review but may not move premium until wording expressly authorizes it.
- **Q4 — Reviewer = Underwriter/claims: DECIDED (BEAAA-7794).** `recommended_disposition` + `affected_party` co-signed as the minimum claim-adjudication fields. Claims Architect owns the output object detail (WS-3).
- **Q5 — Log integrity = trust-for-MVP (documented limitation):** v0.1 trusts customer-supplied logs. A customer could suppress their own incidents → adverse-selection / claims-fraud vector. **Source-signing (attest log integrity at emission) is deferred to v0.2** and tracked as a known limitation, per founder direction.

## 8. Rollback

v0.1 is additive and behind a feature flag; the probe defaults to **local-only / no transmit** until §7 Q2 is ratified. Disabling the outbound sink reverts to a pure local analyzer with zero egress. No Scanner code is touched → no BackBond Score / pricing impact.
