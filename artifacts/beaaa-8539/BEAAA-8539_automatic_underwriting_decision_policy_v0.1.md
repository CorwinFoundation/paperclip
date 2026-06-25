---
artifact_type: other
owner: underwriter
persona_audience: internal
phase: 1
canonical_terms_only: true
brand_backbone_version: "v1.5"
brand_backbone_sha256: "b1ae308e0f150ff5d0a6bdf05071ee98540e296bb3a86a4626b03041a266838f"
brand_backbone_verified: true
focus_claim: "This artifact turns a BackBond Scanner envelope into an auditable automatic underwriting decision without letting the engine bind a real policy before Phase 1 human sign-off."
contracts_referenced: ["are-scanner-api-v1@v1.0", "uw-rubric-supervision-v1@v1.0", "uw-referral-triggers-v0@v0.1"]
ticket_refs: ["BEAAA-8539", "BEAAA-8537", "BEAAA-8499", "BEAAA-8496", "BEAAA-8398", "BEAAA-8223", "BEAAA-4658", "BEAAA-4660", "BEAAA-7313"]
proof_points_cited:
  - "BEAAA-8539 issue context: Phase 1 boundary is auto-resolve clear eligible / clear decline, B/C human route, and no autonomous real-policy bind without human sign-off."
  - "BEAAA-8499 scan-to-bind decision tree v0.1: decision states and reason-code envelope for AUTO_BIND, CONSTRAINED_BIND, REFER_TO_UW, and REFUSE."
  - "BEAAA-8223 pricing-mapping validation: bind path consumes admittance_decision, applicability_record, coverage_caveat, measured dimensions, N/A dimensions, and all-N/A pillar counts."
  - "BEAAA-4658 pricing-input framework: five-field underwriting packet is posture, rate factor, attachment-point ladder, conditions, and decline thresholds."
  - "BEAAA-4660 claim-impact premium math: A/B eligibility, C referral, and D decline posture preserve loss-ratio adequacy; D-band risks are not rescued by price alone."
  - "BEAAA-7313 carrier-paper gap pack: Preferred A and Standard B are delegated-review eligible when evidence is complete; Constrained C is referral; Decline D is not bindable under the standard ladder."
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted:
  - "Internal underwriting policy artifact; Brand Backbone was still verified because this policy can be excerpted into customer-bound or carrier-bound material later."
  - "No scoring weights, covered perils, exclusions, filed rates, or policy wording changed."
  - "Autonomy boundary is intentionally conservative pending Founder confirmation on BEAAA-8537: the engine may auto-resolve eligibility or decline but cannot bind a real policy without human sign-off."
  - "Compliance and Legal awareness required before any production workflow uses auto-resolved eligibility to issue customer-facing bind language."
---

# Automatic Underwriting Decision Policy v0.1

## BLUF

This policy converts a BackBond Scanner envelope into four underwriting classes:

| Policy class | Engine outcome | Human loop | Premium posture |
| --- | --- | --- | --- |
| A | `ELIGIBLE_PENDING_HUMAN_BIND` | Human sign-off required before binding a real policy | Filed-rate-ready quote preview |
| B | `REFER_TO_UNDERWRITER_BIND_BLOCKER` | Operational Underwriter review required | Advisory premium band only |
| C | `REFER_TO_UNDERWRITER_REMEDIATION` | Operational Underwriter review required | Remediation / re-scan band only |
| D | `DECLINE_STANDARD_LADDER` | Auto-resolved decline allowed; appeal routes to Underwriter | No standard primary quote |

Phase 1 boundary: no autonomous real-policy bind. A clear A may be auto-resolved as **eligible pending human bind sign-off**. A clear D may be auto-resolved as **declined under the standard ladder**. B and C always route to the operational Underwriter. Founder confirmation on BEAAA-8537 can later promote A from eligible-pending-sign-off to autonomous bind; this v0.1 policy does not.

The machine-readable companion table is `BEAAA-8539_automatic_underwriting_rules_v0.1.yaml`.

## 1. Scope

Applies to pre-bind automatic underwriting decisions from a complete BackBond Scanner envelope. It does not govern claim adjudication, renewal repricing, non-renewal, mid-term repricing, state filing changes, or policy wording.

This policy is a translation layer over `are-scanner-api-v1@v1.0`. The grandfathered field name `are_total` remains the byte-locked score field. Prose and external display use BackBond canon.

## 2. Inputs Consumed

The engine must consume these fields before assigning a policy class:

| Input | Required shape | Use |
| --- | --- | --- |
| `are_total` | 0-100 number | Score threshold and premium-band interface |
| `band` | `A | B | C | D` | Consistency check against score cuts |
| `subscores.GOVERNANCE` | 0-25 number | Governance floor and evidence-gate checks |
| `subscores.ROBUSTNESS` | 0-25 number | Prompt-injection, tool-misuse, unauthorized-action floor |
| `subscores.BIAS_FAIRNESS` | 0-25 number | Discrimination / regulated-decision risk signal |
| `subscores.OPS` | 0-25 number | Monitoring, retention, incident-response, eval-ledger signal |
| `evidence_refs[]` | Complete evidence refs per scored rubric point | Hard quote admissibility gate |
| `uncovered_dimensions[]` | Dimension ids | Worst-case loading, referral, or decline |
| `scanner_version`, `corpus_version`, `model_versions`, `seed`, `run_timestamp` | Present and non-null | Reproducibility gate |
| `applicability_record.measured_dims_count` | Integer | Partial-coverage normalization / admittance floor |
| `applicability_record.na_dims_count` | Integer | Caveat / transparency routing |
| `applicability_record.pillars_with_all_na[]` | Pillar ids | Hard decline unless Head of UW exception |
| `coverage_fraction.by_peril` | Map of six covered events to 0-1 | Per-peril coverage floor |
| `coverage_fraction.overall` | 0-1 | Portfolio evidence sufficiency |
| `governance_evidence.eval_ledger_p4_5` | `present | absent | stale | partial` | Bind-blocking governance gate |
| `peril_findings[]` | Evidence-linked severity by peril | Override for active high-severity exposure |
| `rate_context` | State, class, limit, action volume, tier | Premium-band interface and filed-rate availability |

If any required field is absent, the envelope fails the admissibility gate and cannot produce a bind-ready quote.

## 3. Preflight Admissibility Gates

Preflight gates run before score thresholds. A preflight failure emits a policy class and reason code without continuing to premium computation.

| Gate | Predicate | Policy class | Reason code |
| --- | --- | --- | --- |
| Run pins missing | Any run pin is absent: scanner version, corpus version, model versions, seed, run timestamp | D | `EV.RUN_PIN_MISSING` |
| Evidence trail incomplete | Any scored rubric point lacks a reachable evidence ref | D | `EV.EVIDENCE_REF_INCOMPLETE` |
| All-N/A pillar | Any pillar has no measured signal | D, unless Head of UW exception exists | `EV.PILLAR_ALL_NA` |
| Measured floor miss | `measured_dims_count < 6` | D | `EV.MEASURED_DIM_FLOOR` |
| Per-peril coverage floor miss | Any covered event has `coverage_fraction < 0.50` | C or D by severity | `EV.PERIL_COVERAGE_FLOOR` |
| Overall coverage floor miss | `coverage_fraction.overall < 0.60` | C | `EV.OVERALL_COVERAGE_FLOOR` |
| Eval-ledger missing | `governance_evidence.eval_ledger_p4_5 in absent/stale` | B if otherwise A/B score; C if C score; D if D score | `GOV.EVAL_LEDGER_P4_5_MISSING` |
| Filed-rate unavailable | No approved filed or approved rating path for state/class/limit | B | `RATE.FILED_RATE_UNAVAILABLE` |
| Prohibited use case | Intake class outside current underwriting appetite | D | `SECTOR.PROHIBITED_USE_CASE` |
| Active high-severity exposure | Evidence-linked active high prompt-injection, data leak, unauthorized action, hallucination, discrimination, or drift exposure | D | `PERIL.ACTIVE_HIGH_SEVERITY` |

Preflight D is an underwriting decline under the standard ladder, not a customer-facing statement that the agent is impossible to insure forever. Appeals or remediation route to the operational Underwriter.

## 4. Score-To-Policy Thresholds

After preflight gates pass, the score thresholds assign the base policy class:

| BackBond Score | Scanner band | Base policy class | Engine outcome |
| ---: | --- | --- | --- |
| 85-100 | A | A | `ELIGIBLE_PENDING_HUMAN_BIND` |
| 70-84 | B | B | `REFER_TO_UNDERWRITER_BIND_BLOCKER` |
| 55-69 | C | C | `REFER_TO_UNDERWRITER_REMEDIATION` |
| 0-54 | D | D | `DECLINE_STANDARD_LADDER` |

Band consistency rule: if `band` conflicts with `are_total`, emit D with `EV.BAND_SCORE_MISMATCH`. The operational Underwriter may request a re-run; the engine must not infer the intended band.

## 5. Override Rules

Overrides tighten the class only. The engine cannot improve a risk above the base class created by the score and preflight gates.

| Override | Predicate | Result |
| --- | --- | --- |
| A with residual caveat | A score, complete evidence, `na_dims_count in 1..2`, all N/A dimensions true architecture N/A | A with transparency condition, still human bind sign-off |
| A/B boundary trigger | Existing `uw-referral-triggers-v0@v0.1` boundary-band live-probe predicate fires | B |
| Bind-critical uncovered dimension | Any uncovered dimension touches a covered event, authority control, PII handling, eval ledger, or incident response | B if A/B score; C if C score; D if D score |
| Governance floor | `GOVERNANCE < 12.5` | B if A/B score and evidence remediable; C if score below 70; D if no eval-ledger / no audit trail |
| Robustness weak-link floor | `ROBUSTNESS < 9.375` or any bind-critical robustness dimension raw floor fails | C or D by severity |
| High-stakes regulated use | Medical, legal, financial, HR, insurance, or fiduciary action authority exists | B minimum; no A without Underwriter sign-off |
| Uncapped action volume | Action or token volume cannot be measured or capped | B if remediable; D if irreversible authority exists |
| Prior adverse signal | Applicant or materially similar agent class has incident / near-miss / claim signal | B minimum; D if unresolved high severity |

## 6. Human-In-The-Loop Boundary

| Policy class | Auto-resolve allowed? | Human action |
| --- | --- | --- |
| A | Yes, as `eligible_pending_human_bind`; no real policy bind | Operational Underwriter or authorized human signs bind after checking filed-rate path and capacity |
| B | No | Operational Underwriter accepts constrained bind, requests more evidence, or declines |
| C | No | Operational Underwriter sets remediation tasks and re-scan condition, or declines |
| D | Yes, as standard-ladder decline | Human review only on appeal, strategic exception, or bespoke layered-excess route |

The engine may produce a quote preview for A when all filed-rate inputs are present. The preview must be marked `not_bound` until human sign-off. No binder, certificate, customer-facing coverage promise, or carrier-facing bound-risk entry is created by the engine in v0.1.

## 7. Premium-Band Interface

Pricing math is not reopened here. The policy emits an interface the quote engine or Actuary-owned rate module fills:

```json
{
  "premium_interface_version": "0.1",
  "score": 87,
  "policy_class": "A",
  "rate_context": {
    "state": "string",
    "class": "string",
    "limit": 50000,
    "deductible": 5000,
    "annual_agent_actions": 1000000,
    "product_tier": "Bronze | Silver | Gold"
  },
  "premium_band": {
    "status": "filed_rate_ready | advisory_only | remediation_only | no_standard_quote",
    "band_factor_stub": 0.60,
    "requires_actuary_rate_module": true,
    "quote_bound": false
  }
}
```

Stub mapping:

| Policy class | Premium status | Factor stub | Use |
| --- | --- | ---: | --- |
| A | `filed_rate_ready` | 0.60 | Quote preview only; human bind sign-off required |
| B | `advisory_only` | 1.00 plus caveat uplift when applicable | Underwriter review packet |
| C | `remediation_only` | 2.50 advisory stress band | Remediation / re-scan packet, not bind-ready |
| D | `no_standard_quote` | null | Decline under standard ladder |

The factor stubs mirror existing A/B/C/D posture discipline and are not a filed-rate change. Actuary owns the production rate module and must replace or confirm the stub before production customer quoting. A premium outside the filed rate remains a Founder-only escalation.

## 8. Required Output Envelope

Every decision emits:

```json
{
  "policy_version": "automatic-underwriting-decision-policy@0.1",
  "rules_table_version": "BEAAA-8539_automatic_underwriting_rules_v0.1",
  "decision_id": "stable id",
  "policy_class": "A | B | C | D",
  "engine_outcome": "ELIGIBLE_PENDING_HUMAN_BIND | REFER_TO_UNDERWRITER_BIND_BLOCKER | REFER_TO_UNDERWRITER_REMEDIATION | DECLINE_STANDARD_LADDER",
  "backbond_score": 0,
  "scanner_band": "A | B | C | D",
  "auto_resolved": true,
  "real_policy_bound": false,
  "human_review_required": true,
  "human_review_owner": "operational_underwriter | none_unless_appeal",
  "premium_band": {
    "status": "filed_rate_ready | advisory_only | remediation_only | no_standard_quote",
    "band_factor_stub": 0.60,
    "quote_bound": false
  },
  "fired_rules": [
    {
      "rule_id": "GOV-002",
      "severity": "blocker | referral | condition | transparency",
      "reason_code": "GOV.EVAL_LEDGER_P4_5_MISSING",
      "input_paths": ["governance_evidence.eval_ledger_p4_5"],
      "observed_values": {"governance_evidence.eval_ledger_p4_5": "absent"},
      "evidence_refs": ["artifact_uri"],
      "remediation": "Attach eval ledger with run cadence and last successful re-run.",
      "rescan_required": true
    }
  ],
  "audit": {
    "scanner_version": "string",
    "corpus_version": "string",
    "model_versions": {},
    "seed": 0,
    "run_timestamp": "ISO-8601",
    "evidence_complete": true
  }
}
```

`auto_resolved=true` is permitted only for A eligibility or D decline. `real_policy_bound` is always false in v0.1.

## 9. Implementation Notes For CTO

1. Evaluate rules in this order: run pins, evidence completeness, all-N/A / measured-floor, prohibited use / active high severity, coverage fraction, governance gates, score threshold, overrides, premium interface.
2. Stop on hard D preflight gates. Continue collecting non-hard fired rules for B/C referral packets so the Underwriter receives a complete remediation queue.
3. Preserve all current scoring and pricing math. This policy consumes existing envelope fields and emits a decision envelope.
4. Persist the fired rule ids, input paths, observed values, and evidence refs. A narrative rationale without machine rule ids is not audit-ready.
5. Treat the YAML rule table as the executable source. The Markdown policy is the human-readable authority.

## 10. Coordination Notes

Actuary: premium-band stubs are interface placeholders. Production quote dollars require Actuary confirmation of the rate module and factor mapping.

Compliance and Legal Coordinator: autonomy boundary is conservative. Review required before any production workflow lets an auto-resolved A become customer-facing bind language or a filed quote in a regulated state.

CTO: implement against the YAML table and return any missing field as a contract-gap issue, not an implicit underwriting assumption.

## 11. Pre-Submission Self-Audit

1. One thing this artifact does: defines the deterministic decision policy converting a BackBond Scanner envelope into A/B/C/D underwriting classes, human-routing, premium posture, and audit output.
2. Audience and next action: CTO implements the rule table; Actuary confirms the premium interface; Compliance and Legal review the autonomy boundary before production use.
3. Contract version cited and evidence trail complete: yes, `are-scanner-api-v1@v1.0` cited; this policy requires complete evidence refs before quote output.
4. Loss-ratio implication: conservative; D is not rescued by price, C routes to remediation, B routes to Underwriter, and A still requires human bind sign-off in Phase 1.
5. Rate filing implication: no filed rate or factor changed; premium stubs are explicitly non-production until Actuary / rate module confirmation.
6. Exclusions and defined terms consistency: no exclusions or policy wording changed.
7. "Insure" language check: passed; no prohibited promise language.
8. Glossary check: passed; BackBond canon used, with legacy contract ids only where byte-locked.
9. Carrier Rule check: passed; no carrier named or positioned competitively.
10. CEO / Founder critique pre-empted: yes; the policy encodes the pending-Founder Phase 1 autonomy limit and flags the exact line that would change after confirmation.

