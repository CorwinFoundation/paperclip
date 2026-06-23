---
artifact_type: report
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "Proves the ADR-0007 admittance dial (incl. the ADM.R0 critical-dim knockout) is actually enforced on the production bind path, so the Underwriter never prices a non-bindable agent off the legacy gate."
contracts_referenced: ["are-scanner-api-v1", "uw-rubric-supervision-v1"]
ticket_refs: ["BEAAA-7937", "BEAAA-7886", "BEAAA-7888", "BEAAA-7892", "BEAAA-7893", "BEAAA-7895"]
proof_points_cited: ["ADR-0007 Option-C admittance dial", "ADR-0038 ADM.R0 knockout"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["wiring activation changes production quote outcomes + requires /quote evidence-bundle contract → routed to Underwriter co-sign (operational seat per ADR-0007 §1.3) + CEO pricing gate"]
---

# BEAAA-7937 — Production bind/quote path admittance-enforcement map (Pass-4 §1.1)

**BLUF.** The Pass-4 §1.1 finding is **CONFIRMED**. The only in-repo production
quote path — `POST /quote` → `api.handlers.quote_handler` → `pricing.quote.quote(...)`
— called `quote()` **without** `admittance_decision`, so the ADR-0007 §1.3
admittance schedule (REFUSE/REFER rules `ADM.R1–R6`) **and** the `ADM.R0`
critical-dim knockout (ADR-0038 / BEAAA-7888) were **inert at the HTTP boundary**.
No caller anywhere in the tree constructed an `AdmittanceDecision` and threaded it
into the pricing engine. A candidate wiring fix + a standing regression guard are
on branch `beaaa-7937-admittance-bind-wiring`. **Activation changes production
quote outcomes and depends on the `/quote` evidence-bundle contract → it is the
Underwriter operational seat's deliverable per ADR-0007 §1.3 and is gated on UW
co-sign (+ CEO pricing gate) before merge.**

## 1. Production bind/quote path map

| Path | Constructs `AdmittanceDecision`? | Threads into `quote()`? | Status |
|---|---|---|---|
| `POST /quote` (`api/app.py:208`) → `quote_handler` (`api/handlers.py:146`) → `quote(score, customer, rates=...)` (`api/handlers.py:162`, origin/master) | **No** | **No** | **GAP** |
| `pricing.quote.quote(...)` library API | n/a (consumer) | accepts optional `admittance_decision` | gate present but caller-dependent |
| `underwriting.admittance.evaluate(...)` | **Yes** (the only constructor) | n/a | **never invoked by any production caller** |
| `tests/test_pricing_admittance.py` | Yes | Yes | library-level only; not the HTTP path |

**Verification commands (origin/master @ `222069e`):**

- `grep -rln "AdmittanceDecision" --include=*.py` → only `pricing/quote.py` (consumer)
  and `underwriting/admittance.py` (definition + `evaluate`). No production caller.
- `api/handlers.py:162` (origin/master): `quote(score_input, customer, rates=rates, quoted_at=quoted_at)` — no `admittance_decision`.
- Per `quote.py` contract, absent `admittance_decision` runs the **legacy** path whose
  only pre-condition gate is the D-band / sector / limit `ManualReferral`.

**Secondary finding:** `ADM.R0` / `ADR-0038` / `BEAAA-7888` are **not yet merged**
to origin/master (`_RULE_ORDER` runs `ADM.R1..R8`; no `ADM.R0`, no `adr-0038`).
So R0 is not live as a *rule* yet, but the wiring gap is the load-bearing problem:
when 7888 lands R0, it would still be **inert** on `/quote` without this wiring.
This is exactly why 7937 is a distinct, load-bearing complement to 7888.

## 2. Why the legacy path is unsafe

The legacy gate cannot REFUSE an anti-safety surface (`ADM.R1`), an all-pillar-N/A
score (`ADM.R2`), a thin-coverage score (`ADM.R3`), or a sub-floor score
(`ADM.R5`), and cannot route `[50,69]` to manual review (`ADM.R6`). A 7886-class
finding (exfil critical dim = 0.0 riding `are_total≈96` to AUTO_BIND) reaches a
premium unchecked. Green library tests + a merged rule gave false assurance.

## 3. The fix (candidate — branch `beaaa-7937-admittance-bind-wiring`)

`quote_handler` now **always** constructs the decision and threads it:

```python
decision = admittance.evaluate(score_input, system_prompt_kind=body.get("system_prompt_kind"))
result = quote(score_input, customer, rates=rates, quoted_at=quoted_at, admittance_decision=decision)
```

- `REFUSE` (`Ineligible`) → `200 {status:"ineligible", firing_rule_id, annual_premium_usd:null}` (newly handled).
- `REFER_TO_UW` (`ManualReferral`) → existing `manual_referral` response.
- `CONSTRAINED_BIND` / `AUTO_BIND` → premium, with the f_caveat caveat endorsement.
- `system_prompt_kind` is **not** a Scanner contract output (ADR-0007 §1.4); the UW
  submission record carries it. The handler reads it off the request and falls
  back to the score envelope; absent it, `ADM.R1`/`ADM.R4` simply do not fire.

### Regression guard
`tests/test_beaaa7937_quote_handler_admittance_wiring.py` — the
`..._guard` case spies on `pricing.quote` and **fails if the handler ever calls
`quote()` without an `admittance_decision`**. Verified: it **FAILS on the
origin/master handler** and **PASSES on the fixed handler**. Plus end-to-end
assertions: bmi<50→ineligible(R5), anti_safety→ineligible(R1), [50,69]→manual_referral(R6),
[70,84]+coverage→constrained-bind premium(R7).

## 4. Outcome delta (why this is NOT CTO-unilateral)

Activating enforcement **changes production quote outcomes**:

- **Scoring (`are_total` + subscores): Δ = 0.** No scoring/rubric/judge/corpus/math
  code is touched. The N=5 golden score-drift band is trivially clean.
- **Quote OUTCOMES change** for agents that fail admittance. `pricing/test_pricing.py`
  + `test_v0_1_1_additive.py` carry **9 legacy handler tests** that assert the
  pre-admittance `/quote` behavior (minimal `{bmi,band}` stubs). Under enforcement
  these stubs → `ADM.R3` REFUSE (`under-5-measured`, because they carry no
  `applicability_record`). Bindable goldens G1/G2/G3 → `CONSTRAINED_BIND` with the
  `f_caveat` premium **uplift** vs the legacy no-uplift premium.
- **Contract dependency:** correct production behavior **requires the `/quote`
  request to carry the admittance evidence bundle** (`applicability_record`,
  `diagnostics.coverage_caveat`, and `system_prompt_kind` from the submission
  record). Otherwise enforcement **over-REFUSES** legitimately bindable agents via
  `ADM.R3`. Tightening the `/quote` request shape is an **Underwriter API-contract
  change** (`contracts/are-scanner-api-v1.md`), which requires the UW approval gate.

Per ADR-0007 §1.3 ("filing the dial inside the quote engine … is the operational
seat's [Underwriter's] deliverable") and the issue's own rule ("if the fix changes
any agent's band/price, escalate"), **merge is gated on Underwriter co-sign**
(operational seat) **+ CEO pricing gate**. The candidate code + guard test on the
branch is the concrete artifact for that co-sign; the 9 legacy tests are migrated
as part of the co-signed merge package (they encode the behavior UW must ratify).

## 5. Disposition

- Acceptance #1 (map): **delivered** (this doc).
- Acceptance #2 (proof + test): gap **proven**; standing guard test **added** and
  shown to fail-on-gap / pass-on-fix.
- Acceptance #3 (merge + Δ=0 or escalate): scoring **Δ=0**; quote-outcome/contract
  change → **escalated to UW co-sign + CEO pricing gate** (not CTO-unilateral).
  No `merged_sha` until co-sign lands.
