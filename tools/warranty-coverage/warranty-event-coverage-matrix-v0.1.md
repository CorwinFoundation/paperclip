<!--
artifact_type: coverage_matrix
artifact_id: warranty-event-coverage-matrix-v0.1
owner: cto
persona_audience: internal   # UW / HUW consumption; internal-only
canonical_terms_only: true
brand_backbone_applicable: false
version: 0.1
status: HUW-CONFIRMED (SS4 mapping + fixture_behavioral semantics, 2026-07-06); UW evidence-class co-sign pending (first emitter + v1.11.0 patch)
ticket_refs: [BEAAA-12029, BEAAA-11920, BEAAA-11808, BEAAA-11809]
contract_version: are-scanner-api-v1@v1.10.0 (proposes additive v1.11.0 evidence classes)
scanner_version: 0.5.0
design_of_record: docs/adr/adr-0052-beaaa12029-warranty-event-family-probe-coverage.md
-->

# Warranty Event-Family Coverage Matrix — v0.1

**Purpose.** Maps every Warranty Terms v1 ([BEAAA-11920](/BEAAA/issues/BEAAA-11920)) §4 **covered failure event** to the BackBond Scanner surface that measures it, so the Underwriter and Head of Underwriting can see exactly **which warranty perils are behaviorally measured versus attestation/proxy-only**. Design of record: [ADR-0052](../../docs/adr/adr-0052-beaaa12029-warranty-event-family-probe-coverage.md).

**Provenance grade ladder** (strongest → weakest): `deployment_measured` > `fixture_behavioral` > `indicative` (category_proxy) > `attested`. See ADR-0052 §4.

> **Red line (binding, BEAAA-11920 / BEAAA-12029).** `fixture_behavioral` is a direct behavioral end-state measurement on **BackBond internal reference fixtures** — real behavior, but **NOT external production proof** and **never** rendered as bare `measured` or `deployment_measured`. `indicative` category-proxy results are **never** reported as `measured`. A full-limit bind on any behaviorally-measured-on-fixture peril requires `deployment_measured` (customer-VPC) evidence or an explicit HUW sub-limit.
>
> **HUW-added condition (binding, 2026-07-06, [BEAAA-12054](/BEAAA/issues/BEAAA-12054)).** `fixture_behavioral` is a **pre-bind risk-measurement grade, NOT a claim-adjudication grade.** It measures the agent's disposition against BackBond reference fixtures before bind; it does **not** observe the customer's production incident. A warranty **remedy trigger** on any covered event still requires production runtime/scanner evidence of the actual covered failure per Warranty Terms v1 ([BEAAA-11920](/BEAAA/issues/BEAAA-11920)) §4. The measurement surface (pricing/eligibility) and the adjudication surface (payout) stay cleanly separated — the fixture probe never substitutes for incident evidence on the loss side.

## SS4 covered-failure-event coverage

| # | SS4 event family | Behavioral probe | Evidence class | Provenance grade | Measured vs attestation | Status |
|---|---|---|---|---|---|---|
| 1 | **Wrong-recipient / misdirected action** | `warranty_recipient_integrity_v1` — mock email/CRM/payments, end-state recipient-identity check vs approved recipient set, pass^k=5 | `recipient_integrity` | `fixture_behavioral` | **Behaviorally measured (fixture)** — NEW | Delegated build (child of BEAAA-12029) |
| 2 | **Unauthorized commitment** | `warranty_authority_bound_v1` — mock authority table, end-state check that no commitment exceeds delegated authority / workflow scope, pass^k=5 | `authority_bound_action` | `fixture_behavioral` | **Behaviorally measured (fixture)** — NEW | Delegated build (child of BEAAA-12029) |
| 3 | **Data leak** | `warranty_data_containment_v1` — seeded confidential records w/ canary tokens, end-state egress-disclosure check vs permitted recipient set, pass^k=5 | `data_containment` | `fixture_behavioral` | **Behaviorally measured (fixture)** — NEW | Delegated build (child of BEAAA-12029) |
| 4 | **Hallucinated action** | HaluEval / tool-grounded factuality ([BEAAA-11807](/BEAAA/issues/BEAAA-11807) §3); behavioral upgrade in flight ([BEAAA-12028](/BEAAA/issues/BEAAA-12028)) | `tool_grounded_factuality` | `indicative` (category_proxy) today; `fixture_behavioral` / `deployment_measured` via BEAAA-12028 | **Proxy today** — indicative only, NOT bind-grade alone; behavioral pilot landing under BEAAA-12028 | Proxy shipped; behavioral pilot in flight (BEAAA-12028) |
| 5 | **Prompt injection / adversarial input** | AgentDojo indirect-injection + `prompt_injection` bench (independent 3rd-party benchmark, ROBUSTNESS probe-class match) | ROBUSTNESS probe-class match (existing) | benchmark / `indicative` (3rd-party) | **Behaviorally exercised** via 3rd-party benchmark on fixtures — screening | Shipped |
| 6 | **Regulatory response event** | — none (no scanner behavioral surface: customer notice + vendor incident file + counsel-reviewed record) | — | `attested` | **Attestation-only** | Attestation-only by design |

## Cross-cutting behavioral controls (feed events above, not 1:1 with a single SS4 row)

| Control | Probe | Evidence class | Provenance grade | Maps to |
|---|---|---|---|---|
| Money-movement correctness (right authority, wrong recipient / amount / double-send) | τ-bench money-movement live wrap ([BEAAA-11808](/BEAAA/issues/BEAAA-11808), `evals/perils/money_movement/tau_bench_live.py`) | `transaction_correctness` | `fixture_behavioral` | Sub-mode of events **1** (wrong-recipient) and **2** (unauthorized commitment) on the payments surface |
| Failure-to-stop / runaway (controllability) | Behavioral STOP live driver ([BEAAA-11809](/BEAAA/issues/BEAAA-11809)) | `stop_compliance` | `fixture_behavioral` | Cross-cutting controllability → ADR-0045 `ADM.R0.STOP` admittance knockout (GOVERNANCE), not a single SS4 event |

## Reading the matrix (for Underwriting)

- **Rows 1–3** move from *attestation-only* to *behaviorally measured on fixtures* once the delegated builds land. They give UW a reproducible pass^k=5 end-state signal per peril — but at `fixture_behavioral` grade, so they support **eligibility/screening and sub-limit** decisions, not an unqualified full-limit bind, until a customer-VPC `deployment_measured` run exists.
- **Row 4** (hallucinated action) remains **proxy/indicative** — do not price it as measured.
- **Row 5** (prompt injection) is exercised by an independent third-party benchmark on fixtures — strong screening, still fixture-grade.
- **Row 6** (regulatory response) has **no scanner surface** and is priced on attestation + incident record.

## Change control

- The three new evidence classes (`recipient_integrity`, `authority_bound_action`, `data_containment`) and the `fixture_behavioral` grade are an additive `are-scanner-api-v1 v1.11.0` bump — **Underwriter co-sign required** before that patch merges (evidence_refs is the UW consumption surface).
- ~~The SS4 event → probe → grade mapping in this matrix requires **Head of Underwriting & AI Risk** confirmation~~ — **RECEIVED 2026-07-06** ([BEAAA-12054](/BEAAA/issues/BEAAA-12054); routed here because the parent is outside HUW's write boundary). HUW confirmed the mapping (all six SS4 events covered, no gaps) and the `fixture_behavioral` semantics, **fenced to screening/eligibility + sub-limit only** (never full-limit, never bare `measured`/`deployment_measured`; bar = pass-on-all k=5), and added the pre-bind-not-adjudication condition folded into the Red-line note above. This authorizes the mapping as a HUW-confirmed reference for warranty-state-machine screening/eligibility + sub-limit reasoning; it does **not** authorize any pricing math, sub-limit *value*, or band/weight wiring — that remains a separate UW/HUW + CTO gate (the deferred fixture-grade sub-limit/rating gate tracked on [BEAAA-12054](/BEAAA/issues/BEAAA-12054)).
- No `are_total` / subscore / pillar-weight / band effect anywhere in this matrix (reported-not-scored, ADR-0045).
