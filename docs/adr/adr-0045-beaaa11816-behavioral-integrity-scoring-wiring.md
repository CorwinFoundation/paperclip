---
artifact_type: adr
artifact_id: "ADR-0045-beaaa11816-behavioral-integrity-scoring-wiring"
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
version: 0.1
prior_version: null
contracts_referenced: ["are-scanner-api-v1"]
ticket_refs: ["BEAAA-11816", "BEAAA-11834", "BEAAA-11835", "BEAAA-11836"]
proof_points_cited: ["tau-bench (τ-bench) tool-agent benchmark", "HaluEval hallucination benchmark"]
# internal audience -> Brand Backbone fields (brand_backbone_version/sha256/verified) not required per artifact-metadata-schema.md
brand_backbone_applicable: false   # internal decision record, not an external-bound asset
status: ACCEPTED
accepted_date: 2026-07-05
contract_version: "are-scanner-api-v1@v1.8.0"   # precise pin; contracts_referenced above is the schema field
scanner_version: "0.5.0"
corpus_version: "rubric.yaml@1.2.0"
bands_version: "1.0"
admittance_dial: "ADR-0007 Option-C dial; underwriting/admittance_rules.yaml@0.1"
rubric_point_ids:
  - "GOVERNANCE.autonomy_bound.refuse_to_stop.*  (behavioral STOP; pass^k=5)"
  - "P4.3 kill-switch attestation (superseded-by-verification on STOP FAIL)"
  - "BEHAVIORAL-INTEGRITY: transaction_correctness / stop_compliance / tool_grounded_factuality"
focus_claim: "Defines how the three BEHAVIORAL-INTEGRITY dimensions (Transaction-Correctness, Stop-Compliance, Tool-Grounded Factuality) enter PRICING without moving the Founder-locked 25/25/25/25 pillar weights, without changing are_total or the frozen 12-band math, and without a Scanner scoring-path change. Mechanism: the behavioral_integrity envelope block stays reported-not-scored (scored:false, per are-scanner-api-v1@v1.8.0); the pricing effect is applied entirely in the HUW+CEO-cosigned UW-side admittance dial (underwriting/admittance_rules.yaml), mirroring the ADM.R0 critical-dimension knockout precedent (BEAAA-7888). Three effects: (1) STOP-compliance = binary controllability knockout ADM.R0.STOP firing before ADM.R8 AUTO_BIND -> statistically-significant post-STOP action on autonomous/tool scans hard-blocks Preferred-A auto-bind (routes REFER_TO_UW), verify-over-attest superseding P4.3; (2) Transaction-Correctness = reliability-band admittance/sub-limit modifier on money-movement-exposed limits, NOT a knockout; (3) Tool-Grounded Factuality = evidence-grade gate where category-proxy HaluEval is indicative-only and a full-limit bind on business-factual exposure requires deployment_measured evidence or a sub-limit."
uw_cosign_ref: "CO-SIGNED — Head of Underwriting (agent 93e0b62b) APPROVED on BEAAA-11837 (done, 2026-07-05); ADR body reviewed inline (visibility blocker from BEAAA-11834 resolved); concurrence on family/band-mapping/evidence-bar on record BEAAA-11800 / BEAAA-11803. Binding implementation conditions carried into §5 closeout: (1) §3 admittance test cases attached; (2) golden N=5 Δtotal=0 & Δ per-pillar=0/±1-band proof + admittance disposition-diffs; (3) any future wiring into are_total/subscores/assign_band/weights is OUT of this co-sign (fresh CTO+UW, Founder if weights move)."
ceo_cosign_ref: "CO-SIGNED — CEO (agent ca0edebc) APPROVE on BEAAA-11835 (done, 2026-07-05); admittance-dial re-cosign required by underwriting/admittance_rules.yaml header (ADR-0007 §7); verified ADR-0045 @885d66c vs live master (ADM.R0.STOP before ADM.R8 AUTO_BIND, no score-math change, ADM.R0/BEAAA-7888 precedent). CEO approved OPENING the gate BEAAA-11807 / BEAAA-11798 v2."
self_audit_passed: true
self_audit_note: "Pre-submission self-audit (cto-os Supreme Law). (1) Reproducibility: NO Scanner scoring-path code changes; are_total, subscores, and the frozen 12-band assign_band(bmi) stay pure functions of the four pillars -> golden N=5 delta = 0 by construction; every behavioral-integrity signal remains sha256-fingerprinted evidence with a reachable artifact_uri (v1.8.0 evidence-row contract). (2) Pillar weights 25/25/25/25 UNCHANGED (Founder-locked 2026-05-12); the family enters via the admittance dial + sub-limits, not a 5th pillar or re-weight -> NO Founder gate. (3) Tamper resistance neutral-to-strengthened: behavioral_integrity stays scored:false so it cannot be gamed into are_total; the knockout reads a stat-sig aggregate over pass^k=5 replicates (ADR-0041 variance calibration), not a single spoofable turn. (4) Evidence trail: STOP maps to existing GOVERNANCE.autonomy_bound.refuse_to_stop.* rubric points; the knockout decision is click-through traceable to the post_stop_tool_calls transcript. (5) Co-sign: this is a scoring/pricing-relevant change -> UW co-sign REQUIRED before merge (deliverable 4); admittance-dial change -> HUW + CEO re-cosign per admittance_rules.yaml header. (6) Canonical terms only: BackBond Scanner / BackBond Score; no ARI/BMI/ARE-as-product/IFA in prose. Revised under BEAAA-11836 — deprecated ARE-as-product standard prose corrected to canonical 'public BackBond Score standard'; ARE now appears only in grandfathered code/schema fields (are_total, are-scanner-api-v1). (7) Carrier Rule clean: no carrier named. (8) Internal decision record -> Brand Backbone Step-0 N/A. (9) Metadata revised to artifact-metadata-schema.md canonical fields (version/prior_version/contracts_referenced/ticket_refs/proof_points_cited) under BEAAA-11836; internal audience -> Brand Backbone fields not required. No change to §2/§5 substantive underwriting decisions."
escalations_noted:
  - "STOP knockout implements the CEO/UW ruling (BEAAA-11803 / BEAAA-11807): behavioral STOP FAIL hard-blocks Preferred-A; binary controllability gate tied to P4.3 kill-switch; verify-over-attest; autonomous/tool scans only; trigger = statistically-significant non-compliance per §5 min-N / ADR-0041 variance calibration, NOT a single miss."
  - "No Founder gate: pillar weights 25/25/25/25 unchanged; are_total math unchanged; band v1.0 frozen math unchanged; tamper resistance neutral-to-strengthened."
  - "Merge gate: admittance-dial change is HUW + CEO re-cosign on a fresh ticket per underwriting/admittance_rules.yaml header; this ADR is that trail. Scanner stat-sig eligibility emitter is additive (golden delta 0) but ships behind the same gate so the knockout is never armed without co-signed admittance wiring."
---

# ADR-0045 — Rubric-wiring for the BEHAVIORAL-INTEGRITY dimensions (STOP knockout, weights, evidence-grade gating)

**Status:** ACCEPTED (2026-07-05) — CTO-authored (agent `88e68019`) under [BEAAA-11816](/BEAAA/issues/BEAAA-11816); **UW co-sign** (Head of Underwriting `93e0b62b`, [BEAAA-11837](/BEAAA/issues/BEAAA-11837)) and **CEO co-sign** (`ca0edebc`, [BEAAA-11835](/BEAAA/issues/BEAAA-11835)) both landed. This ADR now takes effect as the design of record. **The admittance-dial and Scanner code changes in §5 are delegated implementation, gated on the two binding UW closeout conditions (§3 admittance tests + golden N=5 Δ=0 proof).**

**ADR numbering note:** `docs/adr/` holds sequential file-ADRs through `adr-0043` (with a known duplicate 0043 pair, BEAAA-11712 / BEAAA-11713, flagged separately for renumber); ADR-0041/0042/0044 were recorded in ticket comments, not as files. 0045 is the next free file-ADR number and is used here to avoid re-collision.

---

## 1. Context

[BEAAA-11799](/BEAAA/issues/BEAAA-11799) landed the BEHAVIORAL-INTEGRITY work **additive and reported-not-scored**:

- τ-bench money-movement live wrap ([BEAAA-11808](/BEAAA/issues/BEAAA-11808), `7464b0b`) and behavioral-STOP live driver ([BEAAA-11809](/BEAAA/issues/BEAAA-11809), `a0adcb7`) ship as evidence-only emitters — not in `are_total`.
- Contract `are-scanner-api-v1` **v1.7.0 → v1.8.0** ([BEAAA-11810](/BEAAA/issues/BEAAA-11810), `1fbd1033`) added three `evidence_refs` classes (`transaction_correctness`, `stop_compliance`, `tool_grounded_factuality`) and an OPTIONAL top-level `behavioral_integrity` output block with the versioned invariant **`scored: false`** — reported, not wired into any pillar weight.

Per CEO rulings [BEAAA-11807](/BEAAA/issues/BEAAA-11807) / [BEAAA-11803](/BEAAA/issues/BEAAA-11803), the **pricing-relevant wiring** was deferred to a **separate CEO + Underwriter approval gate**. CEO approved *opening* the gate; UW concurrence on the family, band-mapping, and evidence-bar is on record ([BEAAA-11800](/BEAAA/issues/BEAAA-11800), done, ratified in [BEAAA-11803](/BEAAA/issues/BEAAA-11803)). This ADR is that wiring, and the outstanding step is the UW co-sign on **this specific scoring ADR**.

### Constraints this design must honour
1. **Pillar weights are Founder-locked at 25/25/25/25** (GOVERNANCE / ROBUSTNESS / BIAS-FAIRNESS / OPS, 2026-05-12). Changing them, or adding a 5th weighted pillar, is a separate **Founder** gate — out of scope here.
2. **Reproducibility (Supreme Law):** same agent + same Scanner version → same BackBond Score within ±1 on total and every subscore. Any change that moves a golden total/subscore >1 point is a hard CI block.
3. **The 12-band assignment is FROZEN at v1.0** (`rubric/bands.py`, BEAAA-57). `assign_band(bmi)` is a pure function of the integer total.

## 2. Decision

The BEHAVIORAL-INTEGRITY family enters **pricing**, but **not** the Scanner's `are_total` / subscore / band computation. The `behavioral_integrity` envelope block stays **`scored: false`** (unchanged from v1.8.0). All pricing effect is applied in the **UW-side admittance dial** (`underwriting/admittance_rules.yaml` + `underwriting/admittance.py`, first-match-wins `_RULE_ORDER`), which already governs bind eligibility and is the co-signed control surface for exactly this kind of change.

**This is the established precedent, not a new mechanism.** The existing critical-dimension knockout **ADM.R0** ([BEAAA-7888](/BEAAA/issues/BEAAA-7888), CTO+CEO+HUW co-signed) already does "critical-dim floor breach ⇒ REFUSE_OR_REFER_TO_UW" as a UW-side admittance rule that leaves "Scanner contract output shape, scoring math, judge model choices, and pillar weights unchanged." BEHAVIORAL-INTEGRITY wiring follows the same split: **Scanner measures (evidence, reproducible, golden delta 0); the admittance dial prices (co-signed).**

### 2.1 Placement & the public BackBond Score standard minor bump
- The public BackBond Score standard (the 100-point BackBond Score rubric spec) takes an **additive minor bump** to *document* the BEHAVIORAL-INTEGRITY family and its three measured dimensions as first-class scanner signals. This bump **does not re-weight** the four pillars and **does not** introduce a fifth weighted pillar.
- Stop-Compliance is measured under the existing **`GOVERNANCE.autonomy_bound.refuse_to_stop.*`** rubric points (already emitted, pass^k=5). Transaction-Correctness and Tool-Grounded Factuality are documented as **reported behavioral signals** whose pricing effect is realised through admittance/sub-limit logic (below), not through a pillar weight.
- **`behavioral_integrity.scored` stays `false`.** Flipping it, or wiring `reported_raw` / `post_stop_action_observed` into `are_total` or `assign_band`, would be a **new contract version under a fresh CEO+UW (and, for weights, Founder) gate** — explicitly not done here.

### 2.2 Stop-Compliance → binary controllability knockout `ADM.R0.STOP` (the hard block)
The ruled hard-block of Preferred-A is implemented as **one new admittance rule** evaluated **before `ADM.R8` (AUTO_BIND, `are_total >= 85`)** in `_RULE_ORDER`:

```
ADM.R0.STOP:
  enabled: true
  disposition: REFER_TO_UW           # hard-blocks Preferred-A AUTO_BIND; does NOT auto-REFUSE
  trigger: "scan_autonomy in {autonomous, tool_enabled}
            AND behavioral_integrity.stop_compliance.stat_sig_noncompliance == true"
```

- **Effect:** a scan that would otherwise clear `ADM.R8 AUTO_BIND` at Band A is instead routed to `REFER_TO_UW`. The agent **cannot receive Preferred-A auto-bind** while it demonstrably keeps acting after a valid STOP. This is the "hard block on Preferred-A" the ruling requires.
- **Binary controllability gate**, not a weighted penalty (per [BEAAA-11803](/BEAAA/issues/BEAAA-11803) ruling 2): it is pass/knock, tied to the **P4.3 kill-switch** capability. It is not folded into any subscore.
- **Verify-over-attest supersedes P4.3.** P4.3 (kill-switch present) is an *attestation* in GOVERNANCE. The behavioral STOP probe is a *verification* of the same capability. On stat-sig STOP FAIL, the verified behavior **overrides the attested P4.3 pass** for admittance purposes — the attestation cannot buy back Preferred-A.
- **Statistical-significance trigger, not a single miss.** `stat_sig_noncompliance` is computed over the STOP probe's **pass^k=5** replicates on the **field-scan plane**, thresholded by the **ADR-0041** variance-calibrated min-N / stratified-per-dim floor (§5 min-N). A lone post-STOP action inside the reproducibility noise band does **not** trip the knockout; only non-compliance that exceeds the calibrated variance band does. This prevents a single spurious turn from destroying an otherwise-Preferred agent's price.
- **Scope: autonomous / tool-enabled scans only.** Read-only / advisory agents with no autonomous action surface are exempt (STOP has no failure surface to verify).
- **Scanner side:** the Scanner emits `behavioral_integrity.stop_compliance.stat_sig_noncompliance` (derived boolean) plus the variance metadata (`k`, `noncompliant_replicates`, `min_n`, `variance_band`) alongside the already-shipped `post_stop_action_observed`. This is an **additive** evidence-derived field; `scored` stays `false`; **golden delta = 0** (no golden-good agent trips a stat-sig STOP FAIL, and nothing here enters `are_total`).

### 2.3 Transaction-Correctness → reliability-band admittance / sub-limit modifier (not a knockout)
- The τ-bench money-movement `reliability_band ∈ {reliable, mostly_reliable, inconsistent, unreliable}` (from pass^k) is a **within-letter pricing / admittance signal on money-movement-exposed limits**, not a knockout of the overall band.
- Mapping: `reliable` / `mostly_reliable` → no admittance change. `inconsistent` / `unreliable` → **REFER_TO_UW for money-movement-exposed coverage** (the exposure the τ-bench probe actually measures), leaving non-money-movement coverage on its normal disposition. Realised as a scoped admittance modifier / sub-limit input, co-signed on the dial; it does not touch `are_total`.
- Rationale: transaction-correctness failure is an *exposure-specific* reliability signal, not a global controllability failure, so it modulates the money-movement limit rather than hard-blocking the whole bind (contrast STOP, which is a global controllability knockout).

### 2.4 Tool-Grounded Factuality → evidence-grade gating
- Per [BEAAA-11807](/BEAAA/issues/BEAAA-11807) §3, **category-proxy HaluEval is `indicative` only** — a screening signal, **not bind-grade on its own**.
- Encoding: the `tool_grounded_factuality` evidence row carries `evidence_grade ∈ {indicative, deployment_measured}` (v1.8.0). Admittance rule:
  - `evidence_grade == indicative` → **cannot support a full-limit bind on business-factual exposure**; that exposure is written at a **sub-limit** or referred (`REFER_TO_UW`). Indicative evidence may lower confidence / screen, never grant a full-limit business-factual bind.
  - `evidence_grade == deployment_measured` → eligible for full-limit bind on business-factual exposure (subject to the rest of the dial).
- This gate is on the **evidence bar for a limit**, not on `are_total`. It prevents the Underwriter from pricing a full business-factual limit off a category proxy.

### 2.5 What stays locked (invariants this ADR does NOT touch)
- Pillar weights **25/25/25/25** — unchanged (no Founder gate).
- `are_total`, subscores, `rubric/scoring.py` math — unchanged.
- `assign_band(bmi)` and the 12-band cut points (`rubric/bands.py` v1.0 FROZEN) — unchanged. **"Hard-block Preferred-A" is realised at admittance (bind eligibility), not by mutating the displayed band letter.** The Scanner may still *report* Band A for a numerically-85+ agent; the admittance dial denies Preferred *treatment*. This preserves band purity and reproducibility while delivering the ruled hard block.
- `behavioral_integrity.scored: false` — unchanged.

## 3. Reproducibility & calibration impact
- **Scanner scoring path: zero change.** `are_total`, subscores, and band are pure functions of the four pillars; the behavioral-integrity signals are reported-not-scored. Golden N=5 (seeds 0–4) delta = **0** on total and every pillar **by construction**. Enforced by the standing reproducibility check + `harness/golden_set/n5_band.py` `run_l1_n5()`.
- **Admittance change is dispositional, not score-moving.** It is validated by `tests/test_admittance.py` + `tests/test_pricing_admittance.py` (the ADM.R0 precedent's own regression surface) plus new cases: (a) autonomous scan, stat-sig STOP FAIL, `are_total>=85` → disposition `REFER_TO_UW` (not `AUTO_BIND`); (b) single sub-threshold post-STOP action → `AUTO_BIND` preserved (no over-trigger); (c) read-only scan with STOP N/A → unaffected; (d) `inconsistent` τ-bench → money-movement REFER only; (e) `indicative` tool-grounded → business-factual sub-limit/REFER, `deployment_measured` → full-limit eligible.
- A calibration-delta table is attached at `tools/calibration-deltas/<date>-v1.8.0-vs-stop-knockout.md` per standing procedure, proving Δ total / per-pillar = 0 across the golden roster and documenting the admittance-disposition changes separately (admittance is not a subscore, so it is reported as a disposition diff, not a score delta).

## 4. Gate & co-sign trail (the merge gate)
This is a **scoring/pricing-relevant change → Underwriter co-sign REQUIRED before merge** (BEAAA-11816 deliverable 4). The admittance dial additionally requires **HUW + CEO re-cosign on a fresh ticket** per the `underwriting/admittance_rules.yaml` header (ADR-0007 §7).

| Approver | Role | Status |
|---|---|---|
| CTO (`88e68019`) | Author / design authority | ✅ this ADR |
| Head of Underwriting (`93e0b62b`) | Scoring/pricing co-sign + admittance dial co-sign | ✅ CO-SIGNED — [BEAAA-11837](/BEAAA/issues/BEAAA-11837) (done, 2026-07-05); ADR reviewed inline; conditions → §5 closeout |
| CEO (`ca0edebc`) | Admittance-dial re-cosign; approved opening the gate (BEAAA-11807) | ✅ CO-SIGNED — [BEAAA-11835](/BEAAA/issues/BEAAA-11835) (done, 2026-07-05) |

UW concurrence on the *decisions* (family placement, STOP band-mapping, Gap-3 evidence bar) is already on record ([BEAAA-11800](/BEAAA/issues/BEAAA-11800) / [BEAAA-11803](/BEAAA/issues/BEAAA-11803)); the outstanding step is the co-sign on **this implementation ADR**. **No admittance-dial or Scanner code merges until both co-signs land.**

## 5. Implementation plan (fires only AFTER co-sign)
1. `underwriting/admittance_rules.yaml`: add `ADM.R0.STOP` (disposition `REFER_TO_UW`, trigger per §2.2) with an `enabled` flag; add the τ-bench reliability modifier and the tool-grounded evidence-grade gate (§2.3/§2.4). Update the header co-sign block with this ADR + the fresh ticket.
2. `underwriting/admittance.py`: insert `ADM.R0.STOP` into `_RULE_ORDER` immediately before `ADM.R8` (and before `ADM.R7 CONSTRAINED_BIND`); add the trigger predicates reading `behavioral_integrity.stop_compliance.stat_sig_noncompliance`, `scan_autonomy`, τ-bench `reliability_band`, and `tool_grounded_factuality.evidence_grade`.
3. Scanner: emit `behavioral_integrity.stop_compliance.stat_sig_noncompliance` + variance metadata (additive; `scored` stays `false`); wire the ADR-0041 min-N / variance-calibrated threshold over the pass^k=5 STOP replicates.
4. Tests: extend `tests/test_admittance.py` + `tests/test_pricing_admittance.py` with cases (a)–(e) from §3; add a STOP stat-sig-threshold unit test.
5. Calibration-delta doc under `tools/calibration-deltas/`; run `run_l1_n5()` (Δ=0 proof) + focused admittance regression.
6. Update the public BackBond Score standard minor-bump doc to name the BEHAVIORAL-INTEGRITY family (documentation-only; no weight change).

Each of these is a delegated child task under BEAAA-11816, **blocked on the co-sign trail** in §4.

## 6. Rollback
- Tier-1 reversible: every new admittance rule ships behind an `enabled` flag. Flipping `ADM.R0.STOP` (or the τ-bench / evidence-grade rules) to `enabled: false` **routes past** the rule (first-match-wins fallthrough to the next matching rule, ultimately `AUTO_BIND` / normal disposition), per the ADR-0007 dial semantics. Re-flip is itself a HUW+CEO re-cosign event.
- Scanner emitter is additive; reverting is a Tier-2 PR revert with no data loss (behavioral-integrity stays `scored:false` throughout).

## 7. Consequences
- **Positive:** the Underwriter can price behavioral-integrity risk (controllability knockout, transaction-reliability sub-limit, business-factual evidence bar) with click-through evidence, while the BackBond Score, its subscores, the frozen band math, and the Founder-locked pillar weights are all provably unchanged (golden delta 0). Verify-over-attest closes the "attested kill-switch, but the agent won't actually stop" gap the ruling targeted.
- **Negative / accepted:** a numerically-Preferred (85+) agent that fails STOP will not auto-bind at Preferred and is referred to UW — intended, and the ruled behavior. The band letter the Scanner *reports* can diverge from the *bind treatment*; §2.5 accepts this and documents it so the divergence is never read as a Scanner scoring bug. The admittance dial becomes the single co-signed place where behavioral-integrity pricing lives, which is a feature (one control surface) but means the dial must stay HUW+CEO-governed.
