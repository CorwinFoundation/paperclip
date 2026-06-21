---
artifact_type: adr
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "Hard Guarantee #3 (scan envelope `model_versions` populated) is reclassified from a v0 production acceptance criterion to a v1 real-adapter wrap-ticket acceptance criterion, because the v0 synthetic probe path (`synthetic_v0_probe_run_fn` + `v0_mock_runner_factory`) is structurally incapable of populating it — there is no real upstream model under test in v0."
contracts_referenced: ["are-scanner-api-v1@v1.3.0"]
ticket_refs: ["BEAAA-5325", "BEAAA-5324", "BEAAA-5326", "BEAAA-7229"]
proof_points_cited:
  - "BEAAA-5324 structural finding: v0 production wiring (`synthetic_v0_probe_run_fn` + `v0_mock_runner_factory` in `api/scan_runtime_factory.py`) cannot populate `model_versions` — the synthetic walker never invokes a real model and the mock runner echoes its prompt."
  - "Founder ratification 2026-06-21 via BEAAA-7229 request_confirmation interaction d151516e (accepted 2026-06-21T19:36:48Z), clearing the BEAAA-5326 CEO board-accept loop."
non_score_moving: true
contract_output_shape_unchanged: true
classification: governance / acceptance-criteria reclassification (doc-only; no code change, no scoring math change, Δ=0)
version: 1.0
prior_version: null
self_audit_passed: true
escalations_noted:
  - "Reproducibility (Supreme Law) is preserved in v0 by the remaining determinism-tuple fields (scanner_version, corpus_version, seed); model_versions becomes load-bearing only when a real adapter is wired in v1."
  - "When the v1 real-adapter wrap-ticket lands (AnthropicRunner / OpenAIRunner / HTTP shim replacing v0_mock_runner_factory), HG#3 reverts to a hard acceptance criterion and `model_versions` MUST be populated from the live adapter's reported model id(s)."
---

# ADR-0035 — BEAAA-5325 Defer Hard Guarantee #3 (`model_versions` populated) to the v1 real-adapter wrap-ticket

## BLUF

**Decision:** Hard Guarantee #3 of `contracts/are-scanner-api-v1.md` — *every scan envelope MUST populate the `model_versions` provenance field* — is reclassified from a **v0 production acceptance criterion** to a **v1 real-adapter wrap-ticket acceptance criterion**.

**Rationale (BEAAA-5324):** v0 production wiring runs the synthetic probe path — `synthetic_v0_probe_run_fn` yields a fixed handful of `(dim_id, partial_row)` tuples and `v0_mock_runner_factory` returns a `MockRunner` that echoes its prompt. No real upstream model is ever invoked, so there is no model identity to stamp into `model_versions`. Requiring HG#3 as a v0 production gate is therefore structurally unsatisfiable, not a defect to be fixed within v0.

**Scope:** This is a **governance / acceptance-criteria reclassification only**. No code changes, no scoring-math changes, no contract wire-shape changes. The pre-bind score, pillar subscores, band, confidence, and rate book are untouched — **Δ=0 by construction**.

**Authority:** Founder-ratified 2026-06-21 via BEAAA-7229 `request_confirmation` interaction `d151516e` (accepted 19:36:48Z), which cleared the BEAAA-5326 CEO board-accept loop. Founder is the top approval gate; this ratification satisfies the change-control regime for the reclassification.

## Context

`contracts/are-scanner-api-v1.md` §Hard guarantees (CTO) carries, from the v1.1.0 base, Clause #3: the scan-result envelope MUST populate the provenance / reproducibility fields, including `model_versions`, that form the Supreme-Law determinism tuple `(scanner_version, corpus_version, model_versions, seed)`.

BEAAA-5324 found that v0 production (`POST /scan`) is wired to `synthetic_v0_probe_run_fn` + `v0_mock_runner_factory` (see `api/scan_runtime_factory.py`). These exist specifically so the v0 process-model topology and the contract's `incomplete: false` requirement can be exercised **before** a real model adapter is wrapped. The synthetic walker does not call out to any model; the mock runner is a prompt-echo. Consequently `model_versions` has no real value to carry in v0.

Two ways to read HG#3 against this reality:
1. **Treat HG#3 as a v0 gate** → v0 can never pass acceptance, blocking the v0 production posture that is otherwise complete. This is the status quo that produced the BEAAA-5326 escalation loop.
2. **Treat HG#3 as a v1 gate** → v0 is accepted with a `model_versions` sentinel (empty list) understood to mean "no real model under test"; HG#3 becomes hard the moment a real adapter is wired. This is the decision recorded here.

## Decision detail

- **v0 acceptance:** `model_versions` MAY be an empty list `[]` in v0 envelopes. v0 reproducibility is still enforced on the remaining tuple fields (`scanner_version`, `corpus_version`, `seed`), which are fully determined by the synthetic path; the N=5 calibration delta on the golden set is unaffected.
- **v1 acceptance (wrap-ticket):** when `v0_mock_runner_factory` is replaced by a real adapter (`AnthropicRunner` / `OpenAIRunner` / HTTP shim against the customer endpoint), `model_versions` MUST be populated from the adapter's reported model id(s), and HG#3 becomes a hard release gate again. The v1 real-adapter wrap-ticket owns this acceptance criterion.

## Consequences

- **Positive:** unblocks the v0 production acceptance posture (clears BEAAA-5326); makes the contract's acceptance criteria honest about what v0 can structurally guarantee; localizes the `model_versions` obligation to the ticket that actually introduces a real model.
- **Neutral:** no behavioural change — v0 already emits `model_versions: []` from the synthetic path; this ADR makes that conformant rather than a defect.
- **Watch:** the v1 real-adapter wrap-ticket MUST re-assert HG#3. Recorded in this ADR's `escalations_noted` and to be carried as an explicit acceptance row on that ticket so the obligation is not lost in the v0→v1 transition.

## Reproducibility / Supreme Law note

Deferring HG#3 does **not** weaken the Supreme Law in v0: with a deterministic synthetic runner, identical `(scanner_version, corpus_version, seed)` reproduce an identical envelope within ±1 point. `model_versions` only adds discriminating power once a real, versioned model is under test — i.e. in v1. No tamper-resistance guarantee is reduced (HG#3 is a provenance/reproducibility clause, not a tamper clause), so this is not a "tamper-resistance reduction" change class.
