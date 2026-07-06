---
artifact_type: adr
artifact_id: "ADR-0051-beaaa12028-tool-grounded-factuality-pilot"
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
version: 0.1
prior_version: null
contracts_referenced: ["are-scanner-api-v1"]
ticket_refs: ["BEAAA-12028", "BEAAA-11802", "BEAAA-11808", "BEAAA-11816"]
proof_points_cited: ["tau-bench (τ-bench) tool-agent benchmark", "HaluEval hallucination benchmark"]
brand_backbone_applicable: false   # internal decision record, not an external-bound asset
status: ACCEPTED
accepted_date: 2026-07-06
contract_version: "are-scanner-api-v1@v1.8.0"
scanner_version: "0.5.0"
corpus_version: "rubric.yaml@1.2.0"
focus_claim: "Builds the remaining test-coverage-v2 Gap-3 residual: a tool-grounded-factuality pilot on the SAI multi-turn runner + mock-tool harness (BEAAA-11802). A business-factual agent reads a mock pricing spreadsheet / inventory DB via the read-only reference `catalog` surface; a deterministic (no-LLM) verifier asserts every business-factual number in the agent's OUTPUT is grounded in a number the tools RETURNED (output verification) and that the agent consulted the source (end-state), across K=5 replicates — the same live-runner + pass^k + end-state pattern as the tau-bench money-movement wrap (BEAAA-11808). The pilot emits `tool_grounded_factuality` evidence rows at `evidence_grade = deployment_measured` (are-scanner-api-v1 v1.8.0 §1). It is import-only, additive, reported-not-scored per ADR-0045 — NOT wired into are_total / any pillar weight / band; golden N=5 delta = 0 by construction. Red line honoured: category-proxy HaluEval stays `indicative`; this deployment-plane measurement is what earns `deployment_measured`."
self_audit_passed: true
self_audit_note: "Pre-submission self-audit (cto-os Supreme Law). (1) Reproducibility: import-only pilot; NO Scanner scoring-path code touched. Deterministic MockAgentSession policies + deterministic seeded catalog adapter + no-LLM numeric-grounding verifier + sha256 over canonical JSON -> byte-reproducible across N=5 (ledger fingerprints + evidence sha256 identical run-to-run, asserted by test_deterministic_across_runs). are_total / subscores / assign_band unchanged -> golden N=5 delta = 0 by construction (same class as BEAAA-11808 evidence-only emitter). (2) Pillar weights 25/25/25/25 UNCHANGED; nothing enters a pillar or a 5th weighted dimension -> NO Founder gate. (3) Evidence trail: every row carries class=tool_grounded_factuality, evidence_grade=deployment_measured, a reachable artifact_uri, and a sha256 fingerprint (transcript + grounding_check) -> click-through, no trust-me points. (4) Tamper resistance: the grounded corpus is derived from the recorded tool-read LOG (structured tool results), never from agent free text, so injected transcript text cannot move a verdict; verifier is a pure function, no judge to talk out of a ruling. (5) Canonical terms: BackBond Scanner / BackBond Score; ARE appears only in grandfathered schema/code fields (are_total, are-scanner-api-v1). (6) Carrier Rule: no carrier or customer named; catalog data is synthetic fixture. (7) Internal decision record -> Brand Backbone Step-0 N/A. (8) Co-sign: additive-evidence + reported-not-scored, nothing auto-consumes the output -> per v1.8.0 patch §4 precedent the Underwriter is NOTIFIED, co-sign NOT required to merge the pilot; the co-sign that DOES matter (whether this methodology legitimately constitutes `deployment_measured` for a bind decision) is offered to UW before the admittance dial actually reads these rows for pricing (§4 below)."
escalations_noted:
  - "No Founder gate: pillar weights 25/25/25/25 unchanged; are_total math unchanged; band v1.0 frozen math unchanged; behavioral_integrity stays scored:false."
  - "UW co-sign offered (not blocking merge): the evidence-grade GATE is already co-signed (ADR-0045 §2.4, UW+CEO on BEAAA-11837/11835). This pilot is delegated implementation of that gate — it PRODUCES the deployment_measured evidence the gate consumes. Merging it changes no pricing today (import-only, nothing wired). Before the admittance dial reads these rows to grant a full-limit business-factual bind, UW should co-sign that this verifier methodology legitimately earns `deployment_measured`."
---

# ADR-0051 — Tool-grounded factuality pilot (Gap-3 `deployment_measured` evidence)

**Status:** ACCEPTED (2026-07-06) — CTO-authored (agent `88e68019`) under [BEAAA-12028](/BEAAA/issues/BEAAA-12028). Founder request (Ari, 2026-07-06). Delegated implementation of the co-signed [ADR-0045](adr-0045-beaaa11816-behavioral-integrity-scoring-wiring.md) §2.4 evidence-grade gate; import-only, reported-not-scored, golden delta 0.

## 1. Context

ADR-0045 §2.4 established the **Gap-3 evidence-grade gate**: a `tool_grounded_factuality` evidence row carries `evidence_grade ∈ {indicative, deployment_measured}`; `indicative` (category-proxy, e.g. HaluEval) **cannot support a full-limit bind** on business-factual exposure (sub-limit or REFER), while `deployment_measured` is full-limit-eligible. The v1.8.0 contract added the `tool_grounded_factuality` evidence class and the optional `evidence_grade` tag. **Until now no probe emitted `deployment_measured`** — only the HaluEval `indicative` screen existed. The Underwriter therefore had to sub-limit or refer every business-factual exposure. That is the residual this pilot closes.

The SAI multi-turn runner + mock-tool harness ([BEAAA-11802](/BEAAA/issues/BEAAA-11802)) and the tau-bench money-movement live wrap ([BEAAA-11808](/BEAAA/issues/BEAAA-11808)) are on master. This pilot reuses that substrate verbatim.

## 2. Decision

Build a tool-grounded-factuality pilot on the **same live-runner + pass^k + end-state pattern** as the tau-bench wrap:

- **Surface** — a new read-only reference adapter `catalog` (`harness/mock_tools/adapters/catalog.py`): a deterministic mock pricing spreadsheet / inventory DB. Operations `lookup_price`, `get_stock`, `list_catalog`, all READ_ONLY, no scope. Each read returns **only** the field it is named for, so the set of numbers the agent may faithfully quote is exactly the set the tools returned it.
- **Pilot mapping** — `harness/mock_tools/pilots/tool_grounded_factuality_v1.yaml` binds the agent's tool names to the `catalog` surface.
- **Verifier** (`evals/perils/tool_grounded_factuality/verifier.py`) — deterministic, no-LLM. Two verdicts per run:
  - `output_grounding` (OUTPUT verification): every numeric token in the agent's output is grounded — it appears in a tool-returned value or in the user's own turns. An ungrounded number is a **fabrication**. Identifier digits (e.g. the `1001` of `SKU-1001`) are excluded by construction; comma/decimal formatting normalises via `Decimal` equality.
  - `source_consulted` (END-STATE): the agent performed ≥1 successful `catalog` read before answering. An agent that quotes numbers without reading grounds on nothing.
- **Wrap** (`pilot_live.py`) — drives K=5 replicates, fresh harness + session each, `pass^k` requires every replicate to fabricate zero numbers AND consult the source; emits a words-first `grounding_band` and `tool_grounded_factuality` evidence rows (transcript + grounding_check), each `sha256`-fingerprinted with a reachable `artifact_uri`.

### 2.1 What earns `deployment_measured` (the red line)

`EVIDENCE_GRADE = "deployment_measured"` is stamped on every row this pilot emits, and **only** this deployment-plane measurement earns it. The distinction from HaluEval is substantive, not cosmetic:

| | HaluEval (`indicative`) | This pilot (`deployment_measured`) |
|---|---|---|
| What runs | static QA pairs scored offline | the **actual agent** on the live multi-turn runner |
| Ground truth | a fixed reference-answer corpus | the numbers the agent's **own tools returned** this session |
| What is checked | text similarity to a category proxy | every number in the agent's output is grounded in retrieved data |
| Reproducibility | corpus-pinned | byte-reproducible pass^k=5 on the deployment plane |

The pilot measures the real agent grounding real tool reads, so its evidence is deployment-measured. The category proxy screens; it never measures. **HaluEval is never stamped `deployment_measured`.**

## 3. Reproducibility & scoring impact

- **Import-only, reported-not-scored.** Nothing in the scoring pipeline imports this package. `are_total`, subscores, and `assign_band(bmi)` are unchanged pure functions of the four pillars → **golden N=5 delta = 0 by construction** (same class as BEAAA-11808). No calibration-delta table is required for a change that touches no scoring math, rubric, corpus, or judge.
- Deterministic policies + seeded adapter + no-LLM verifier + sha256 canonical JSON → identical ledger fingerprints and evidence hashes run-to-run (asserted in `tests/test_tool_grounded_factuality_beaaa12028.py::test_deterministic_across_runs`).

## 4. Gate & co-sign

- **Merge:** additive-evidence + reported-not-scored, nothing auto-consumes the output → per the v1.8.0 patch §4 precedent, **Underwriter NOTIFIED, co-sign NOT required to merge** the pilot. No Founder gate (weights/`are_total`/band untouched).
- **Before pricing consumption:** the evidence-grade *gate* is already UW+CEO co-signed (ADR-0045 §2.4). What remains for UW is a methodology co-sign — a confirmation that this verifier legitimately constitutes `deployment_measured` — **before the admittance dial reads these rows to grant a full-limit business-factual bind**. Offered to Head of Underwriting on BEAAA-12028; not blocking this import-only merge.

## 5. Rollback

Additive; Tier-2 PR revert. The `catalog` adapter, the pilot YAML, the `tool_grounded_factuality` package, and the test are all new files; reverting the commit removes them with no data loss and no scoring-path change (nothing referenced them).
