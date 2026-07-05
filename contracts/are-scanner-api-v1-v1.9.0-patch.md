---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
brand_backbone_applicable: false   # internal contract patch, not an external-bound asset
focus_claim: "v1.9.0 contract patch (are-scanner-api-v1 v1.8.0 -> v1.9.0) documents the ONE additive, OPTIONAL, REQUEST-side field surface_seed on POST /scanner/v1/scans (and /rescans), landed with the BEAAA-11864 probe-surface-rotation impl (ADR-0046, CTO ruling A on BEAAA-11863; merged origin/master @1be55bf). surface_seed is a per-run seed for probe-surface paraphrase rotation, STRICTLY SEPARATE from the scoring RNG seed; it selects which paraphrase template the Prober sends over the wire and NEVER feeds the scoring RNG or any scoring math. Classification: additive-minor, request-side only. NO response/envelope field added, NO evidence_refs change, NO rubric wiring, NO pillar re-weight, NO change to how are_total is computed. Rotation-OFF is byte-identical to the canonical probe surface; golden N=5 delta = 0 by construction (verified at BEAAA-11864). This patch is a REGISTRY-HONESTY follow-up: the field already shipped in api/openapi.yaml @1be55bf but was absent from the versioned contract doc; it does NOT gate any code merge (that merge already landed and is fully co-signed)."
contracts_referenced: ["are-scanner-api-v1@v1.8.0 (shipped) -> v1.9.0 (proposed additive minor)", "scanner-implementation-supervision-v1@v1.0", "uw-rubric-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-11863", "BEAAA-11864", "BEAAA-11859", "BEAAA-11857", "BEAAA-11735", "BEAAA-11868", "BEAAA-11869"]
proof_points_cited:
  - "CTO ruling A on BEAAA-11863 (decision gate for BEAAA-11859 audit): wire probe surface rotation into the live walker; classified rule-change (tamper-resistance + reproducibility, ADR-tier); A *meets* canon §7 — NO §7 amendment, NO Founder gate."
  - "BEAAA-11864 impl MERGED origin/master @1be55bf (ADR-0046): ScanRequest.surface_seed added (STRICTLY SEPARATE from scoring seed); orchestrator best-effort stamps active_runner.surface_seed; walker _rotate_probe_surface -> paraphrase_probe -> runner.complete; edge derives a per-run seed from sha256(scan_id) (anti-harvest) when the field is omitted and rotation is enabled. api/openapi.yaml carries the surface_seed request property (component: descriptive, optional, integer)."
  - "Gate #1 reproducibility (CTO): rotation-ON golden N=5 (seeds 0-4) delta = 0/0 across all pillars + total; scripts/release_gate_beaaa11864_rotation_on_n5.py passed=True, re-verified by CTO in an isolated worktree. Rotation is upstream of scored outputs, so it cannot move a score for fixed behaviour (goldens score pre-recorded eval_outputs; the walker is never invoked on the golden path)."
  - "Gate #2 Underwriter cross-run band (non-golden customer agents): co-signed on BEAAA-11869 (done). Gate #3 CTO ADR-0046 + merge-diff co-sign: GRANTED (BEAAA-11863 relay comment 0153205f, read-the-diff verification)."
version: "v1.9.0"
prior_version: "are-scanner-api-v1@v1.8.0"
self_audit_passed: true
self_audit_note: "Pre-submission self-audit. (1) Focus claim cites the CTO ruling (BEAAA-11863 A), the merged impl commit (1be55bf) with the exact seed-separation shape, and all three landed gates (repro N=5=0, UW 11869, CTO gate #3 co-sign 0153205f). (2) Carrier Rule clean — no carrier or customer named. (3) Canonical terms only — 'the Scanner' / 'Prober' / 'probe surface'; no BMI / ARI / ARE-as-product / BEAI / IFA in body prose (api field names such as are_total/seed are literal identifiers, not prose). (4) Internal patch, not an external-bound asset -> Brand Backbone Step-0 gate N/A (persona_audience: internal). (5) Supreme Law / reproducibility: the four reproducibility fields (scanner_version, corpus_version, model_versions, seed) are UNCHANGED and remain required; surface_seed is a SEPARATE request input that never derives the scoring RNG; rotation-OFF is byte-identical and rotation-ON golden N=5 delta = 0 by construction (verified BEAAA-11864). NO scoring-path code touched by THIS patch (contract prose + history row only; openapi already shipped @1be55bf). (6) Tamper resistance: STRENGTHENED — surface rotation closes the ~100% single-scan static-probe-surface harvest hole that the BEAAA-11859 audit confirmed; this is the whole point of ruling A. (7) NO pillar-weight / rubric-weight / scoring-math / judge change; surface_seed is a request input, NOT a response evidence-ref field, so the Founder freeze on the response evidence-ref set is not touched -> NO Founder gate; pillar weights stay 25/25/25/25 Founder-locked. (8) Co-sign: contract disposition is the CTO's per ADR-0046 and this patch is the CTO-requested registry-honesty follow-up (BEAAA-11863 gate #3 comment, Condition 1); it does NOT hold the already-landed, fully co-signed code merge. (9) Rollback: contract-doc only; Tier-0 doc revert. The underlying field is toggle-gated at runtime (ARE_PROBE_PARAPHRASE_RANDOMIZATION default off) and omitting surface_seed from a request is inert."
escalations_noted:
  - "Version selection — v1.9.0 (v1.8.0 + 1 minor). Registry check: contracts/ carries patch files through are-scanner-api-v1-v1.8.0-patch.md; this is the next unused minor. Bumped exactly once."
  - "NO Founder gate: surface_seed is a REQUEST-side input, not a response evidence-ref/output field; pillar weights 25/25/25/25 UNCHANGED (Founder-locked 2026-05-12); are_total computation UNCHANGED; tamper resistance strengthened (anti-harvest)."
  - "NO new UW decision on THIS doc: the pricing-relevant cross-run band on non-golden customer agents was the Gate #2 decision and is already co-signed (BEAAA-11869, done). This patch documents an already-decided, already-merged field."
---

# Contract patch — `are-scanner-api-v1.md` v1.8.0 -> v1.9.0

**Status:** DRAFT authored by Scanner Engineer (agent `d8bbc3b5`) under the CTO's Condition-1 directive on [BEAAA-11863](/BEAAA/issues/BEAAA-11863) (gate #3 co-sign, Condition 1: "author the next additive-minor contract patch doc … so the registry stays honest"). Registry-honesty follow-up to the already-merged, fully co-signed [BEAAA-11864](/BEAAA/issues/BEAAA-11864) impl. Lands on canonical master on CTO diff co-sign + green CI (`golden scanner regression`, `python pytest`).

**Design authority:** CTO (contract design). Contract disposition for `surface_seed` is fixed by ADR-0046 and restated in the [BEAAA-11863](/BEAAA/issues/BEAAA-11863) gate #3 comment: **additive, optional, request-side ⇒ clean additive-minor bump, no Founder gate** (it is not a response/`evidence_refs` field; the Founder freeze is on the response evidence-ref set, not request inputs).

**Change classification:** additive minor. One new OPTIONAL request-side field. No existing field renamed or removed. No response/envelope change. Consumers that omit `surface_seed` receive a **byte-identical v1.8.0 response and, with rotation disabled, a byte-identical probe surface**. Tier-0 doc-revert rollback (the runtime field is itself toggle-gated).

**The load-bearing invariant (read first):** `surface_seed` is **strictly separate** from the scoring `seed`. It seeds *which paraphrase template the Prober sends over the wire* — it is **upstream of the scored output** and **never** feeds the scoring RNG, `are_total`, any pillar, or any band. Rotation is *behaviour-preserving on the score*: for a fixed agent behaviour the paraphrased probe elicits a semantically-equivalent response that scores identically (golden N=5 delta = 0, verified at [BEAAA-11864](/BEAAA/issues/BEAAA-11864)). This is exactly why ruling A *meets* canon §7 rather than amending it.

---

## §0. Why now — the field shipped; the registry must name it

The [BEAAA-11859](/BEAAA/issues/BEAAA-11859) audit confirmed a tamper-resistance defect on the live scan path: `paraphrase_probe()` had **zero non-test callers**, so the Prober sent each probe's *canonical* prompt verbatim. After one scan, a vendor logging its own endpoint reconstructs ~100% of the static probe surface and pre-trains its agent to pass. CTO ruling **A** on [BEAAA-11863](/BEAAA/issues/BEAAA-11863) wired real per-run rotation (ADR-0046); the impl merged to canonical master @ `1be55bf`, adding the `surface_seed` request property to `api/openapi.yaml`. What was missing is the **versioned contract-doc entry** naming that field, so the `v1.x` registry does not silently diverge from the shipped OpenAPI. This patch is that entry.

## §1. New request field — `surface_seed` (additive, optional)

`POST /scanner/v1/scans` (and, by inheritance, `POST /scanner/v1/rescans`) request body gains ONE new **OPTIONAL** property. The v1.1.0 input recap ("byte-identical to v1.1.0") is extended by exactly this field; every existing consumer that omits it is unaffected.

```jsonc
{
  // ... all existing v1.1.0 request fields unchanged (scan_mode, k_replicates, seed, …) ...
  "surface_seed": 1234567890          // OPTIONAL; additive v1.9.0; integer; per-run probe-surface rotation seed
}
```

| Property | Type | Required | Semantics |
|---|---|---|---|
| `surface_seed` | integer | **No** (optional) | Per-run seed for probe-surface **paraphrase rotation**. **STRICTLY SEPARATE from `seed`** (the scoring RNG). Selects which paraphrase template the Prober renders for each probe; **never** affects scoring math, `are_total`, any pillar, or any band. **Omit** ⇒ when rotation is enabled the server derives a fresh per-run seed from the server-minted `scan_id` (`sha256(scan_id)`-derived) so the wire surface is unpredictable to a harvester; **supply an explicit integer** ⇒ replay a prior scan's exact wire surface. When rotation is **disabled** (default, see §3) the field is **inert** and the canonical probe surface is emitted verbatim. |

Input hygiene (edge, per [BEAAA-11864](/BEAAA/issues/BEAAA-11864)): a non-integer or boolean `surface_seed` is rejected with **`422 surface_seed_invalid`**. Anti-harvest defaulting uses the server-minted `scan_id`, not a client value; exact replay is available only by supplying the explicit body seed.

## §2. Separation from the scoring `seed` — the reproducibility invariant

`seed` (scoring RNG, §Reproducibility) and `surface_seed` (probe-surface rotation) are **two independent inputs**:

- The scoring RNG is **never** derived from `surface_seed`. The orchestrator best-effort stamps `active_runner.surface_seed` (mirroring the optional `set_scan_deadline` idiom) so the Prober-side walker can read it **without widening the fixed `ProbeRunFn(runner)` signature**; the Scorer subprocess never sees it.
- Template selection is a **pure function of `(probe_id, surface_seed, bank_version)`** — deterministic and replayable, with no live LLM at selection time.
- Therefore: same agent behaviour ⇒ same score, regardless of `surface_seed`. Golden N=5 (seeds 0–4) delta = **0/0** across all pillars + total (Gate #1, CTO-re-verified). The four reproducibility fields (`scanner_version`, `corpus_version`, `model_versions`, `seed`) are **unchanged and remain required**.

## §3. Runtime toggle + rollback

Rotation is gated by `ARE_PROBE_PARAPHRASE_RANDOMIZATION` (**default off**; also honoured via `ARE_PROBE_SAMPLING`). With the toggle off, `paraphrase_probe(enabled=False)` returns `text == canonical` and the emitted probe surface is **byte-for-byte** the canonical v1.8.0 surface (Tier-1 runtime rollback). This contract-doc patch itself rolls back by Tier-0 doc-revert; removing it does not touch any code, and the shipped OpenAPI field remains valid and inert under the toggle.

## §4. `api/openapi.yaml` — already shipped @ `1be55bf`

No OpenAPI change is required by this patch: the `surface_seed` request property already landed with the [BEAAA-11864](/BEAAA/issues/BEAAA-11864) impl (`api/openapi.yaml`, request schema, optional integer with the seed-separation description). This patch brings the **contract doc** into alignment with that shipped schema. (`info.version` in openapi tracks the API-doc line independently; the authoritative contract version is this patch file.)

## §5. Approval-gate disposition

- **This doc = additive-minor, request-side, registry-honesty follow-up.** No scoring change; golden N=5 delta = 0 by construction. The pricing-relevant decision (cross-run band on non-golden customer agents) was **Gate #2** and is already co-signed on [BEAAA-11869](/BEAAA/issues/BEAAA-11869) (done); no new UW decision arises here.
- **Contract disposition is the CTO's** (ADR-0046 + [BEAAA-11863](/BEAAA/issues/BEAAA-11863) gate #3). This patch lands on the CTO's diff co-sign; it does **not** hold the already-merged, fully co-signed code (`1be55bf`).
- **No Founder gate:** `surface_seed` is a request input, not a response evidence-ref field; pillar weights unchanged; `are_total` computation unchanged; tamper resistance strengthened (anti-harvest).

## §6. Reproducibility / calibration impact

- This patch touches **contract prose + the history table only** — no scanner code, no rubric, no corpus, no judge, no openapi (already shipped). Golden N=5 delta = 0 **by construction**.
- The field it documents ([BEAAA-11864](/BEAAA/issues/BEAAA-11864)) landed with rotation-ON golden N=5 = 0/0 already verified and CTO-re-verified.

## §7. Changelog / version-history

Appended to the base contract `§Versioning — history` table:

| Version | Date | Bump class | Authoritative source |
|---|---|---|---|
| **v1.9.0** | **2026-07-05** | **additive minor — OPTIONAL request-side `surface_seed` (per-run probe-surface paraphrase rotation; STRICTLY SEPARATE from scoring `seed`; anti-harvest); no response/scoring change** | **This patch; impl BEAAA-11864 / ADR-0046 (merged `1be55bf`); CTO ruling A BEAAA-11863; gates: repro N=5=0 (CTO), UW band BEAAA-11869, CTO diff+ADR co-sign BEAAA-11863#0153205f** |

**No silent additions:** the single new field is enumerated above with its type, optional status, seed-separation invariant, and toggle-gated inertness. Nothing on the scoring path or the response envelope changes.
