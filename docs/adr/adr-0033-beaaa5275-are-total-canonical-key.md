---
artifact_type: adr
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-5275 (A-001) renames score()'s canonical total key from 'bmi' to 'are_total', aligning score() output with are-scanner-api-v1 §Output. The emitted integer is byte-identical, so the change is Supreme-Law delta=0 by additive identity. Amendment 1 (BEAAA-7073): the 'bmi' alias is PERMANENT, not a one-release shim — the 8 locked beaaa-387 baselines carry 'bmi' only, so removal cannot remove the dual-spelling read."
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-5275", "BEAAA-5267", "BEAAA-5264", "BEAAA-5276", "BEAAA-5282", "BEAAA-7073"]
proof_points_cited: ["ScoringTests 13/13 green on rebased tree", "envelope/contract suite 65/65 green", "HoUW ACCEPT-with-R1 06-17", "cherry-pick adc33f4 onto origin/master 7f55595 auto-merged clean"]
version: 0.2
prior_version: 0.1
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

Decision: **APPROVE and merge** the BEAAA-5275 (A-001, P0 [REPRO]) change — `score()` emits `are_total` as its canonical total key and additionally emits `bmi` as an alias bound to the same integer (originally scoped as a one-release deprecation shim; **superseded — see Amendment 1: the alias is permanent**); `harness/contract_envelope.py` reader is updated to prefer `are_total` and fall back to `bmi`. This aligns `score()`'s in-process output with `are-scanner-api-v1 §Output`, which already specifies `are_total`. **Supreme-Law delta = 0 by additive identity** — the emitted integer is byte-identical, no scoring math / weights / judges / bands / probes / corpus touched — so the "could move scores" trigger is not met and the N=5 golden-set obligation is retired by a diff-level identity proof plus an in-test equality assertion (`are_total == bmi`).

## Reasoning

**The defect (A-001, source BEAAA-5264).** `score()` emitted `{"bmi": bmi_int, ...}` as its primary total field. The contract `are-scanner-api-v1 §Output` specifies `are_total`. The only place the two were reconciled was inside `harness/contract_envelope.py` via `score_result.get("bmi", 0)`. Any **direct** caller of `score()` — replay tooling, claims-defense scripts, ad-hoc tests — that looked up `are_total` received `None` → presented as score 0 → band D-. That is a silent reproducibility/pricing hazard: the same agent reads as a different score depending on which code path consumes `score()`.

**Why `are_total` canonical + `bmi` shim (not a hard rename).** A hard rename would break any in-flight reader still keyed on `bmi` in the same release. Emitting both keys, bound to the identical `bmi_int`, makes every reader correct during the deprecation window regardless of which key it uses. The shim was explicitly marked for removal after one release (see R1 / follow-up). **Amendment 1 (BEAAA-7073) reverses that:** removal was measured as impossible, and the alias is retained permanently.

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

## R1 (carried) — shim removal follow-up — **CLOSED, REFUSED (Amendment 1, BEAAA-7073)**

HoUW ACCEPT carried R1: the `bmi` shim is a one-release deprecation window. Action: open a follow-up issue to remove the `"bmi"` shim line from `score()` after the next release ships, leaving `are_total` as the sole key. Tracked as a child of BEAAA-5275 as **BEAAA-7073**.

**R1 is discharged as REFUSED. `bmi` is a permanent legacy alias, not a deprecation shim.** See Amendment 1 below for the measurement and the ruling. No further removal follow-up should be opened against this ADR; re-deriving this is the failure mode Amendment 1 exists to prevent.

## Cluster Context (NOT ratified here — own-heartbeat ratification)

Same rollup BEAAA-5267 / Top-10 reproducibility series, re-assigned to CTO by CEO cmt 2a0bfbd5:

| Carve | Severity | Change | Branch | UW state | Ratified by this ADR? |
|---|---|---|---|---|---|
| BEAAA-5275 (A-001) | P0 [REPRO] | `bmi`→`are_total` canonical key + shim | `beaaa-5275-are-total-rename` (commit `adc33f4`) | HoUW ACCEPT-with-R1 | **YES** |
| BEAAA-5276 | P0 [REPRO] | require `corpus_version` at `score()` boundary | `beaaa-5276-corpus-version-enforce @ e52db3b` | HoUW ACCEPT | No — own heartbeat |
| BEAAA-5282 | P1 [REPRO] | `scan_log` storage → JCS canonical | `beaaa-5282-jcs-storage @ c1b466f` | HoUW ACCEPT | Separate — amends ADR-0009 (JCS), already on master |

Each carve carries its own ADR/amendment and own co-sign disposition: 5282 amends `adr-0009-scan-log-jcs-storage-format.md` (§4.2, merged to master); 5276 (corpus_version boundary) ratifies in its own heartbeat. This ADR is authoritative for BEAAA-5275 only.

## Amendment 1 (BEAAA-7073, 2026-09-18) — `bmi` is a **permanent legacy alias**

**Decision: `keep-alias`.** The `"bmi"` key emitted by `score()` is retained indefinitely. `are_total` is the canonical name for everything written since ADR-0033 v0.1. R1's removal instruction is withdrawn. Ruled by the CEO on BEAAA-7073; docs-only change, no code touched.

### Why removal is not available

**A locked baseline is not editable by definition, so a removal cannot actually remove the alias — it only stops writing it down.** The 8 locked deterministic baselines under `artifacts/beaaa-387/<fid>/baseline.json` carry the total under `bmi` **only**:

| fixture | `bmi` | `are_total` |
|---|---|---|
| B1 | 33 | absent |
| B3 | 60 | absent |
| G1 | 81 | absent |
| G2 | 78 | absent |
| G3 | 75 | absent |
| M1 | 58 | absent |
| M2 | 46 | absent |
| M3 | 60 | absent |

(B2 holds no `baseline.json` — breach fixture only — hence 8, not 9.) They are pinned by `manifest.json` with `lock_source: "deterministic_replay"`, `locked_at: 2026-05-15T20:37:30+00:00`, against `rubric_version 1.1.0` / `scanner_version 0.4.0`, and hashed in `trusted-replay-manifest-v1.json`.

**Consequence — the dual-spelling read is permanent, and it is already in the release gate.** `scripts/release_gate_n5.py` reads both spellings in one file:

- line 307 — `"total": float(baseline["bmi"])` — the locked baseline, bracket access, `bmi` spelling mandatory.
- line 318 — `"total": float(payload["are_total"])` — live output, canonical spelling.

Any code that compares live output against a locked baseline must therefore speak both names forever. Dropping the emitted alias does not retire that requirement; it only removes the writer while leaving every reader.

### Why it is refused on cost as well

Removing the alias fixes no hazard — the two keys are bound to the same `bmi_int`, so no reader can observe a discrepancy.

Against that zero benefit, the cost side re-measured on master `abdfa2f9a` (2026-09-18). Predicate is a literal dict read of the key — `["bmi"]` or `.get("bmi"` — over `*.py`, excluding `artifacts/` and `.worktrees/`:

| surface | lines | files |
|---|---|---|
| production packages (`api`, `harness`, `report_card`, `underwriting`, `rubric`) | 23 | 13 |
| `scripts/` (excl. `archive/`) | 75 | 32 |
| `scripts/archive/` | 16 | — |
| `tests/` | 26 | 13 |
| **total** | **140** | **67** |

A zero-benefit change at that cost is refused on the merits.

> **Correction of record.** The BEAAA-7073 decision card quoted "24 production readers and 54 lines across 30 test files." The production figure is right (23 lines / 13 files, within rounding of how readers were counted); the test figure is not reproducible — the true test surface is 26 lines across 13 files, and the 30-file number appears to have conflated `tests/` with `scripts/`. The corrected total is **larger** than the card's, so the refusal stands on stronger ground than it was argued on, not weaker.

### Scope boundary — what this amendment does NOT do

**`report_card/sensitivity.py:33` is deliberately untouched.** It reads `base_bmi = int(score_response["bmi"])`, and `report_card/render.py:791` passes it `_display_sr`, whose `bmi` is the **display-reweighted** total. Repointing that line to `are_total` is *not* a mechanical alias cleanup and must never ride in one: whether the sensitivity panel's base is correct is a live scoring-correctness question that exists independently of the alias. It is tracked separately and is out of scope here.

### Note on the display-weighted dict (BEAAA-7073, PR #602, merged `213f7133`)

Two alias-adjacent reads were corrected *before* this ruling, both measured delta-zero:

- `harness/verdict_envelope.py:421` — `int(score_response.get("are_total", score_response.get("bmi", 0)))`. Canonical key first. Delta-zero in all cases: both keys are bound to the same int, and either ordering falls back to the other spelling.
- `report_card/render.py:731` — `"are_total": _d_bmi` set beside the `bmi` alias in `_display_sr`. Delta-zero today because no reader takes `are_total` off that dict (`_display_sr` is consumed only at lines 738–740 for `bmi`/`band`/`pillars`, and at 791 by `render_sensitivity_block`). Before this line existed the dict carried a **reweighted `bmi` beside an un-reweighted `are_total` inherited from `score_response`** — the stale pair that would have made any future repoint of `sensitivity.py:33` silently wrong. Setting both to `_d_bmi` removes that trap without changing any current output.

### Co-sign

- CEO — ruling of record on BEAAA-7073 (2026-09-18), amendment authority for an internal naming contract over scoring code. No spend, no external commitment, no customer-facing byte, no pillar weight / band-cut / tamper-resistance change.
- CTO — ADR owner of record.
- Underwriter — **not re-triggered.** `scanner-implementation-supervision-v1@v1.0` classifies output-*shape* changes as UW co-sign. This amendment changes no emitted key and no integer; it withdraws a planned future removal and is docs-only. The original v0.1 output-shape co-sign (HoUW ACCEPT-with-R1, 2026-06-17) stands, with its R1 now discharged as refused.
- Founder gate — not required, unchanged from v0.1.

**Supreme Law: delta = 0.** No file outside `docs/adr/` is modified by this amendment.

## Related

- Contract: `are-scanner-api-v1 §Output` (specifies `are_total`).
- Supervision: `scanner-implementation-supervision-v1@v1.0` (output-shape change ⇒ CTO + UW co-sign).
- Source finding: BEAAA-5264 A-001. Rollup: BEAAA-5267 Top-10 #2.
- ADR-0008 (BEAAA-510) — `bmi-scanner/`→`are-scanner/` dir + term rename; this ADR completes the *output-key* half of the bmi→are migration.
