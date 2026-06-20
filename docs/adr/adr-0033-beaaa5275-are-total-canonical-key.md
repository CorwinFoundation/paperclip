---
artifact_type: adr
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-5275 (A-001) renames score()'s canonical total key from 'bmi' to 'are_total' with a one-release 'bmi' shim, aligning score() output with are-scanner-api-v1 §Output. The emitted integer is byte-identical, so the change is Supreme-Law delta=0 by additive identity."
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-5275", "BEAAA-5267", "BEAAA-5264", "BEAAA-5276", "BEAAA-5282"]
proof_points_cited: ["ScoringTests 13/13 green on rebased tree", "envelope/contract suite 65/65 green", "HoUW ACCEPT-with-R1 06-17", "cherry-pick adc33f4 onto origin/master 7f55595 auto-merged clean"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
co_sign_required:
  - CTO (88e68019-1cb6-4c4f-83e1-9fd340fb6821) — ADR author + final co-sign (this document).
  - Underwriter — scoring output-shape change per scanner-implementation-supervision-v1@v1.0. SATISFIED: HoUW ACCEPT-with-R1 recorded 2026-06-17 (CEO triage cmt 2a0bfbd5, BEAAA-5275).
  - Founder gate — NOT required (no pillar weight change, no band-cut change, no tamper-resistance reduction, no customer-data handling expansion).
classification_per_supervision_contract:
  row: "Scoring output shape change (output JSON key)"
  classification: rule-change
  cto_cosign_required: true
  uw_cosign_required: true
---

# ADR-0033 — BEAAA-5275 (A-001) `score()` Canonical Total Key `are_total` + `bmi` Shim

> **Numbering note:** CEO cmt 2a0bfbd5 suggested folding the BEAAA-5267 carve cluster into a "single ADR-0009." That number was already taken by the long-standing `adr-0009-scan-log-jcs-storage-format.md` (scan_log persistence invariants, BEAAA-1271, amended by BEAAA-5282). To avoid an ADR-number collision, this 5275 decision is recorded as the next free number, **ADR-0033**. The cluster is documented per-carve, not in one container: 5282 amends ADR-0009 (JCS); 5276 carries its own ADR/co-sign. This ADR is authoritative for **A-001 / BEAAA-5275 only**.

## BLUF

Decision: **APPROVE and merge** the BEAAA-5275 (A-001, P0 [REPRO]) change — `score()` emits `are_total` as its canonical total key and additionally emits `bmi` as a one-release deprecation shim bound to the same integer; `harness/contract_envelope.py` reader is updated to prefer `are_total` and fall back to `bmi`. This aligns `score()`'s in-process output with `are-scanner-api-v1 §Output`, which already specifies `are_total`. **Supreme-Law delta = 0 by additive identity** — the emitted integer is byte-identical, no scoring math / weights / judges / bands / probes / corpus touched — so the "could move scores" trigger is not met and the N=5 golden-set obligation is retired by a diff-level identity proof plus an in-test equality assertion (`are_total == bmi`).

## Reasoning

**The defect (A-001, source BEAAA-5264).** `score()` emitted `{"bmi": bmi_int, ...}` as its primary total field. The contract `are-scanner-api-v1 §Output` specifies `are_total`. The only place the two were reconciled was inside `harness/contract_envelope.py` via `score_result.get("bmi", 0)`. Any **direct** caller of `score()` — replay tooling, claims-defense scripts, ad-hoc tests — that looked up `are_total` received `None` → presented as score 0 → band D-. That is a silent reproducibility/pricing hazard: the same agent reads as a different score depending on which code path consumes `score()`.

**Why `are_total` canonical + `bmi` shim (not a hard rename).** A hard rename would break any in-flight reader still keyed on `bmi` in the same release. Emitting both keys, bound to the identical `bmi_int`, makes every reader correct during the deprecation window regardless of which key it uses. The shim is explicitly marked for removal after one release (see R1 / follow-up).

**Why the envelope reader prefers `are_total`.** On current master the envelope already emits `"are_total"` downstream (line 365) but still *read* the total via `bmi`. Switching the reader to `score_result.get("are_total", score_result.get("bmi", 0))` makes the envelope authoritative on the new canonical key while remaining correct for any score dict that only carries the legacy key.

**Supreme-Law disposition.** Per the standing reproducibility procedure, a change is gated only if it "could move scores." Here the integer bound to both keys is the same `bmi_int` the function already computed; the diff adds a key and an alias and changes a reader's lookup order — it cannot move any subscore or total. I retired the N=5 golden-set run with: (a) a diff-level additive-identity proof (the new key and the shim are both `= bmi_int`); and (b) an in-test invariant `assertEqual(out["are_total"], out["bmi"])` that fails loudly if the two ever diverge. No UW pricing gate beyond the output-shape co-sign (satisfied), no Founder escalation.

## Verification (this heartbeat)

Rebase of the original branch commit `adc33f4` (cut from stale base `67679ade`) onto current canonical master `origin/master = 7f55595`:

- Cherry-pick `adc33f4` onto `7f55595` **auto-merged clean** (3-way merge resolved the `contract_envelope.py` reader move from old line 234 to current line 342; no manual conflict). Result is the exact intended 3-file diff, +11/−2.
- `harness/contract_envelope.py:342` → `bmi = int(score_result.get("are_total", score_result.get("bmi", 0)))`. No bare `get("bmi", 0)` reader remains.
- `rubric/scoring.py:357-358` → emits `"are_total": bmi_int` then `"bmi": bmi_int  # deprecation shim`.
- `tests/test_scoring.py` → new `test_score_emits_are_total_canonical_key` asserting `"are_total" in out` and `out["are_total"] == out["bmi"]`.
- `python3 -m pytest tests/test_scoring.py` → **13 passed**.
- `python3 -m pytest tests/ -k "envelope or contract"` → **65 passed**.

**Note — branch ref hygiene:** the remote branch ref `origin/beaaa-5275-are-total-rename` tip is `8366f92` (a **BEAAA-5278** F-04 JCS-HMAC commit), sitting *on top of* `adc33f4`. Only `adc33f4` is the A-001 change. The merge cherry-picks `adc33f4` **alone** — `8366f92` (BEAAA-5278, not in this cluster) is **not** carried.

## R1 (carried) — shim removal follow-up

HoUW ACCEPT carried R1: the `bmi` shim is a one-release deprecation window. Action: open a follow-up issue to remove the `"bmi"` shim line from `score()` after the next release ships, leaving `are_total` as the sole key. Tracked as a child of BEAAA-5275.

## Cluster Context (NOT ratified here — own-heartbeat ratification)

Same rollup BEAAA-5267 / Top-10 reproducibility series, re-assigned to CTO by CEO cmt 2a0bfbd5:

| Carve | Severity | Change | Branch | UW state | Ratified by this ADR? |
|---|---|---|---|---|---|
| BEAAA-5275 (A-001) | P0 [REPRO] | `bmi`→`are_total` canonical key + shim | `beaaa-5275-are-total-rename` (commit `adc33f4`) | HoUW ACCEPT-with-R1 | **YES** |
| BEAAA-5276 | P0 [REPRO] | require `corpus_version` at `score()` boundary | `beaaa-5276-corpus-version-enforce @ e52db3b` | HoUW ACCEPT | No — own heartbeat |
| BEAAA-5282 | P1 [REPRO] | `scan_log` storage → JCS canonical | `beaaa-5282-jcs-storage @ c1b466f` | HoUW ACCEPT | Separate — amends ADR-0009 (JCS), already on master |

Each carve carries its own ADR/amendment and own co-sign disposition: 5282 amends `adr-0009-scan-log-jcs-storage-format.md` (§4.2, merged to master); 5276 (corpus_version boundary) ratifies in its own heartbeat. This ADR is authoritative for BEAAA-5275 only.

## Related

- Contract: `are-scanner-api-v1 §Output` (specifies `are_total`).
- Supervision: `scanner-implementation-supervision-v1@v1.0` (output-shape change ⇒ CTO + UW co-sign).
- Source finding: BEAAA-5264 A-001. Rollup: BEAAA-5267 Top-10 #2.
- ADR-0008 (BEAAA-510) — `bmi-scanner/`→`are-scanner/` dir + term rename; this ADR completes the *output-key* half of the bmi→are migration.
