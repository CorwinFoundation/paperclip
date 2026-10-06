# BEAAA-38570: rows of the never-captured class a full BEAAA-23944 C1 build makes executable

**Answer: 311 of 1,181** (272 single-field assertions + 39 three-field conjunctions). Without `effect_count` the number is 261. On today's master the class still has 1,181 never-captured rows; none has become `scoring_active` since `d5490ad7`.

## Never-captured filter (step 1)

Join key: `unified-probe-inventory.jsonl.dedupe_sha256` = registry `candidate_dedupe_sha256`. A row is never-captured when `scoring_active` is false in **both** `tools/active_test_registry.py --registry-out` runs, without and with `BACKBOND_FOUNDER_DEV_MODE=1`. This is the rule in `scripts/beaaa35665_scope_refusal_audit.py::never_captured`. Freeze v1.4 joins the inventory on `stable_case_id`, 2,000 of 2,000.

| commit | never-captured | class ∩ never-captured | field assertion | comparator | conjunction |
|---|---:|---:|---:|---:|---:|
| `d5490ad7` (BEAAA-35665 audit) | 1,589 | 1,181 | 887 | 192 | 102 |
| `c8549fd9` (master, 2026-10-06) | 1,413 | 1,181 | 887 | 192 | 102 |

The parent's 1,181 / 887 reproduces exactly. The 176-row drop in never-captured since `d5490ad7` is entirely outside this class. The 280 trace-observation adapter rows did not move any class row to `scoring_active`, so "already executable on today's master" is **0** by the registry's own definition.

## Field classification (step 2)

A row counts only if every asserted field is produced by one of the 11 C1 elements in BEAAA-23944 §1.2. Paired comparators (192) have no field and count 0. 109 field names map; the full mapping is `classify.py:E`, and every row with its per-field element is in `c1_field_join.json`.

| C1 element | field hits in the 311 |
|---|---:|
| E1 tool registry, allow/deny | 54 |
| E2 schema-bound args | 16 |
| E3 scoped authority, tenant boundary | 76 |
| E4 delegated identity | 8 |
| E5 preview-confirm-execute binding | 14 |
| E6 expiring single-use tokens | 6 |
| E7 revocable authority | 1 |
| E8 idempotency, durable result (effect store) | 84 |
| E9 delivery/transfer recipient | 29 |
| E10 terminal state | 79 |
| E11 confirmation boundary | 22 |

Judgement calls a reader should check: `effect_count` (50 never-captured rows) is assigned to E8 because the effect ledger is what produces it; dropping it gives 261. Memory, retrieval/citation, voice/recording, residency, sandbox/process, code-patch, checkpoint/restore, human-takeover, privacy-release and evaluation-integrity fields are outside C1. 37 conjunctions map one or two of three fields and are not counted.

## Against BEAAA-23944 §2.3

The same rule over the whole 1,465-row class gives 345; §2.3 puts a full C1 at 361 of the 2,000. The two agree within 16 rows, so the 12 engineer-day C1 figure is corroborated at field level. 311 is the number to price deliverable 3 against.

Regenerate: `python3 join.py && python3 classify.py` after writing the four registry JSONs (prod/dev at each commit).
