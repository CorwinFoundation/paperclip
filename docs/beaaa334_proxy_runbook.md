# BEAAA-334 — Residential-proxy POC runbook

Operational steps for the CTO heartbeat that resumes after Founders drop Decodo (Smartproxy) creds into `.env`. Mirror this runbook into the POC report comment (AC #4) after a successful run.

## Pre-flight

1. `.env` contains:
   ```
   BEAAA_RESI_PROXY_URL=http://<user>:<pass>@gate.decodo.com:7000
   BEAAA_RESI_PROXY_ROTATE=per_session
   ```
2. Geo pinned to US-resi at the Decodo account level (BEAAA-274 d6ce36f7 sharpened spec).
3. Baseline JSON exists at `artifacts/beaaa-334/baseline_proxy_off.json` (committed; 2 walls).

## Step 1 — verify adapter sees the env var

```bash
cd projects/bmi-scanner
python3 -c "from harness import egress; print('enabled:', egress.is_enabled())"
```

Expected: `enabled: True`. If False, re-source `.env` (`set -a; source .env; set +a`).

## Step 2 — single-call probe (cheap sanity check)

```bash
python3 scripts/beaaa334_wall_regress.py --out /tmp/beaaa334_proxied_smoke.json --skip-chatlab
```

Expected on success: `lift = 2/2 (proxy ON)`, both walls NOT walled.

Failure modes:
- Both walls still walled → proxy not reaching residential pool (check creds, Decodo dashboard for active session).
- Bland NOT walled, Casetext walled → US-resi geo not enforced for Casetext (geo-pin at Decodo account level).
- 407 Proxy Authentication Required → username/password wrong; do NOT log creds.

## Step 3 — full 3-wall regression with comparison

```bash
python3 scripts/beaaa334_wall_regress.py \
  --out /tmp/beaaa334_proxied.json \
  --compare-baseline artifacts/beaaa-334/baseline_proxy_off.json \
  --report-md /tmp/beaaa334_report.md
```

Burns ChatLab loop: 50 sessions × ~2s = ~100s wall time. Watch for:
- ChatLab `empty_rate > 0.20` → proxy IPs are in CF reputation set (try `BEAAA_RESI_PROXY_ROTATE=per_request` to force per-call rotation).
- `passes_ac=True` → proceed to Step 4.
- `passes_ac=False` with lift = 2/3 → still passes AC #3, but document the still-walled vendor in the report.

## Step 4 — reproducibility re-verification (AC #5, the keep/discard gate)

```bash
# N=5 golden-set runs with proxy ON
for i in 1 2 3 4 5; do
  python3 -m harness.pipeline --golden-set --seed 42 \
    --out /tmp/beaaa334_golden_run_$i.json
done
python3 scripts/calibration_delta.py \
  --baseline artifacts/golden_set/release_baseline.json \
  --runs /tmp/beaaa334_golden_run_*.json \
  --tolerance 1.0
```

Expected: every golden total and subscore within ±1 point of baseline across N=5.

If any golden moves > 1 point: **block** keep recommendation. Escalate per cto-os.md (any golden moving > 1 requires Underwriter approval; the proxy is then NOT shipped for calibration runs even if walls clear).

## Step 5 — POC report (AC #4)

The report markdown is already drafted at `/tmp/beaaa334_report.md` by Step 3. Fill in three sections:

- **Vendor cost**: pull from Decodo dashboard. Spend at gate-clear (out of $200 cap) + projected ongoing if approved.
- **ToS posture**: quote Decodo AUP excerpt on QA / security-testing. Note any carve-outs. Cross-reference BEAAA-10 counsel-glance outcome.
- **Recommendation**: keep / discard. Tie to AC #5 outcome.

Then:

```bash
cat /tmp/beaaa334_report.md  # final manual review
# Post as comment on BEAAA-334 via Paperclip API.
```

## Step 6 — close

- If keep: mark BEAAA-334 done. Update BEAAA-337 (golden-set walled-vendor drop) — it `blockedBy: [334, 336]`, so this unblocks it.
- If discard: mark BEAAA-334 done with discard verdict. Escalate BEAAA-337 path (UW co-sign on golden-set drop becomes the active lever).
- Recurring spend, if any, is a separate ask per parent plan §7.

## Rollback

Single env-var unset: `unset BEAAA_RESI_PROXY_URL`. Adapter reverts to bare httpx. Verified by `tests/test_egress.py`.

## Out of scope for this POC

- `scripts/scan_chatlab_ecommerce.py` production ChatLab scan path (uses websockets in addition to httpx; needs `python-socks` lift for WS-over-CONNECT). Deferred to a follow-up post-POC decision once keep/discard is settled.
- HuggingChat web runner via `hugchat` SDK. The SDK uses requests; would route through `HTTPS_PROXY` global env if needed but that bleeds to judge calls — see ADR 0001 reasoning.
