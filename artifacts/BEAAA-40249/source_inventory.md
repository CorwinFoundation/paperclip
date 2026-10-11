# BEAAA-40249 exact-truth source inventory

Inventory date: 2026-10-11 UTC  
Repository candidate: `beaaa-40249-judge-exact@559018d0d`

## Stored sources available in the repository

| Source | Stored artifact | Eligible before cap | Selected | Tuning | Held out | Exact fail selected |
|---|---|---:|---:|---:|---:|---:|
| M false commitments | `tools/full-scan-m-runs/2026-10-10-commitments/false_commitments_openai_gpt-4.1-mini_rows.jsonl` | 765 | 400 | 280 | 120 | 11 |
| M grounded claims | `tools/full-scan-m-runs/2026-10-10-r2/grounded_claims_openai_gpt-4.1-mini_rows.jsonl` | 519 | 400 | 286 | 114 | 73 |
| L pressure pack | `artifacts/BEAAA-39970/pressure_openai_gpt-4.1-mini_details.jsonl` | 600 | 400 | 272 | 128 | 72 |
| **Total** | | **1,884** | **1,200** | **838** | **362** | **156** |

Preparation produced zero rejected rows. The selected exact-fail share is 13%; the 40% target cannot be reached in the M sources because their stored exact-fail populations are smaller than 160 rows per 400-row stratum. All available failures are retained.

## Requested sources not stored in this checkout

No recorded exact-result artifact was found for N money/tau, K egress, O deterministic secrets, E action sandbox beyond the older BEAAA-38925 partial run, H agentic, conv-en canary, CyberSecEval canary, or HarmBench contextual canary. The repository contains source corpora and runners for several of these, but those are not substitutes for the recorded replies/traces and exact outcomes required by this calibration.

The next action is to retrieve the issue attachments or run artifacts for BEAAA-39968, BEAAA-39972, and BEAAA-39979 plus the E/H/conv-en/OSS recorded runs, then join them to the preserved corpus rows with `--input-corpus`. No targets should be rerun.

## Reproduction

```sh
python3 tools/judge_vs_exact.py \
  tools/full-scan-m-runs/2026-10-10-r2/grounded_claims_openai_gpt-4.1-mini_rows.jsonl \
  tools/full-scan-m-runs/2026-10-10-commitments/false_commitments_openai_gpt-4.1-mini_rows.jsonl \
  artifacts/BEAAA-39970/pressure_openai_gpt-4.1-mini_details.jsonl \
  --gold-sample-v2 artifacts/beaaa-39492/gold_sample_v2.xlsx \
  --output artifacts/BEAAA-40249/prepared_available_exact_rows.json \
  --rejection-manifest artifacts/BEAAA-40249/prepared_available_rejections.json
```

Spend: USD 0.00. No provider or target calls were made. Scoring was not changed.
