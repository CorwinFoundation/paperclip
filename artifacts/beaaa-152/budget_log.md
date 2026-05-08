# BEAAA-152 compute budget log

**Cap:** $100 hard (board-approved 2026-05-08 08:02Z, approval `acfc217d`).
**Owner:** CTO. **Source-of-truth:** this file. Update on every paid run.

| date | item | model | tests | cost | running total | run artifact |
|---|---|---|---|---|---|---|
| 2026-05-08 | HaluEval QA smoke (key sanity + wiring) | haiku-4.5 | 5 | $0.000549 | $0.000549 | `artifacts/beaaa-152/runs/halueval_smoke_run.json` |

## Phase 1 plan

| pack | est. tests | est. $/test | est. cost |
|---|---|---|---|
| HaluEval | ~3K | ~$0.005 | ~$15 |
| JailbreakBench | ~0.85K (post-UW exclude) | ~$0.005 | ~$4 |
| Garak (extended) | ~3K | ~$0.005 | ~$15 |
| **Phase 1 subtotal** | **~6.85K** | | **~$34** |

## Phase 2 plan (Garak ext + 5 packs)

Per [BEAAA-152 #document-wsd-phase2-draft](/BEAAA/issues/BEAAA-152#document-wsd-phase2-draft) — total ~3.5K additional tests, ~$17.5 estimated. Phase 2 commit follows UW Phase 2 review pass on PrivacyBench cross-loads + Garak↔PromptInject overlap + atkgen-as-generator.

## Combined target

~10.4K tests · ~$51.5 · ~$48.5 buffer vs $100 cap.

**Smoke-revised projection (2026-05-08):** measured per-call cost on Haiku 4.5 was ~$0.00011 (5 items, $0.000549). Scales linearly to ~$1.10 for 10K items — **roughly 50× under the original ~$51.5 estimate**. The $100 cap is now generous-by-50× rather than tight. Conservative 2× safety margin still leaves us under $5 for the full Phase 1 + Phase 2 set. Will reconcile after each pack run.

## Spend rules

1. Every paid run row appended here BEFORE clicking through to the API.
2. Stop and post on issue if any single run estimate >$10 (sanity check on per-test cost).
3. Stop and post if running total crosses 50% of cap ($50) — board signal.
4. No paid run without an associated bin yaml committed to `rubric/probe_mappings/`.
5. Receipts: link to the run artifact JSON in the table; receipts come from the API provider's billing console at month close.
