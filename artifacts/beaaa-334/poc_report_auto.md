## BEAAA-334 — Residential-proxy POC report (AC #4)

- baseline_run: started 2026-05-14T20:15:41.449575+00:00, lift `0/2`, proxy_enabled=False
- proxied_run: started 2026-05-26T11:31:28.341223+00:00, lift `2/3`, proxy_enabled=True, passes_ac=True

### Per-wall comparison

| Wall | Family | Baseline | Proxied | Lift | Latency (B → P) |
|---|---|---|---|---|---|
| `bland` | cloud-IP edge refusal (Tier 1) | walled | NOT walled | yes | 403.0 ms → 1791.4 ms |
| `casetext` | cloud-IP edge refusal (Tier 1) | walled | NOT walled | yes | 107.2 ms → 3470.4 ms |
| `chatlab` | CF WAF adaptive flag (Tier 2) | — | walled | no — still walled | — → empty_rate=1.0, refused=0 |

**Headline lift:** 2/3 walls cleared with proxy ON (AC #3 gate: ≥ 2/3). **`passes_ac = True`**

### Vendor cost (fill in from signup)

- Vendor: _Decodo (Smartproxy) — primary pick per BLUF_
- Plan: _PAYG, $X/GB at signup tier_
- Spend to gate-clear: _$X.XX out of $200 POC cap_
- Projected ongoing if approved: _$X/mo at expected scan volume_

### Latency overhead (computed from baseline / proxied elapsed_ms above)

- Bland.ai overhead: +1388.4 ms (344.5%)
- Casetext overhead: +3363.2 ms (3137.3%)

### ToS posture (fill in from vendor AUP read at signup)

- AUP excerpt covering QA / security-testing usage: _quote here_
- Counsel-glance via BEAAA-10: _outcome / pending_
- Carve-outs or explicit forbids: _none observed | list_

### Recommendation

- **Keep / Discard:** _fill in_
- **Rollback posture:** `BEAAA_RESI_PROXY_URL` unset = default OFF; no calibration or runtime impact when disabled.
- **Reproducibility band re-verification (AC #5):** _attach N=5 golden-set delta against pre-proxy baseline; if any golden moves > 1 ARE point, release blocks per cto-os.md._

— CTO (88e68019)
