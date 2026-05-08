# BEAAA-152 compute budget log

**Cap:** $100 hard (board-approved 2026-05-08 08:02Z, approval `acfc217d`).
**Owner:** CTO. **Source-of-truth:** this file. Update on every paid run.

| date | item | model | tests | cost | running total | run artifact |
|---|---|---|---|---|---|---|
| 2026-05-08 | HaluEval QA smoke (key sanity + wiring) | haiku-4.5 | 5 | $0.000549 | $0.000549 | `artifacts/beaaa-152/runs/halueval_smoke_run.json` |
| 2026-05-08 | HaluEval QA n=100 (first real measurement, n=50 source × 2) | haiku-4.5 | 100 | $0.024047 | $0.024596 | `artifacts/beaaa-152/runs/halueval_qa_n100_run.json` |
| 2026-05-08 | HaluEval dialogue+summ+general n=300 (first pass on 3 remaining HaluEval splits) | haiku-4.5 | 300 | $0.105940 | $0.130536 | `artifacts/beaaa-152/runs/halueval_dialogue_summ_general_n300_run.json` |
| 2026-05-08 | TruthfulQA n=100 (25 b2b + 25 residual, factual+incorrect each — first new pack source measured) | haiku-4.5 | 100 | $0.013289 | $0.143825 | `artifacts/beaaa-152/runs/tqa_n100_run.json` |
| 2026-05-08 | HaluEval QA additional n=300 (different seed; scales qa total to 400 measured) | haiku-4.5 | 300 | $0.071002 | $0.214827 | `artifacts/beaaa-152/runs/halueval_qa_n300_run.json` |
| 2026-05-08 | JBB n=80 (first T5 / P3.x measurement; 7 UW categories incl privacy cross-load) | haiku-4.5 | 80 | $0.032127 | $0.246954 | `artifacts/beaaa-152/runs/jbb_n80_run.json` |

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

**n=100 reconciliation (2026-05-08):** with the HaluEval `knowledge` context field included (per the HaluEval evaluation protocol), per-call cost rose to ~$0.00024 — the prompt grew from ~70 tokens (smoke) to ~206 tokens. Scales to ~$1.44 for the full ~6K HaluEval test points, ~$2.50 for full Phase 1 + Phase 2 with safety margin. Still ≥40× under the $100 cap; still no spend-gate trip likely. Smoke estimate was anchored on knowledge-free prompts; future pack-runs with similarly-sized context (HaluEval summarization, FActScore biographical) will land closer to $0.0002–$0.0003/call, packs without context (JBB, AdvBench, PromptInject) closer to $0.0001/call.

## Spend rules

1. Every paid run row appended here BEFORE clicking through to the API.
2. Stop and post on issue if any single run estimate >$10 (sanity check on per-test cost).
3. Stop and post if running total crosses 50% of cap ($50) — board signal.
4. No paid run without an associated bin yaml committed to `rubric/probe_mappings/`.
5. Receipts: link to the run artifact JSON in the table; receipts come from the API provider's billing console at month close.
