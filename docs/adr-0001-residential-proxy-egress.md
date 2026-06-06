# ADR 0001 — Residential-proxy egress adapter for ARE Scanner Path B

**Status:** Accepted (CTO unilateral, within $25K vendor authority, per `cto-os.md` Decision Authority).
**Date:** 2026-05-14.
**Issue:** BEAAA-334. **Parent:** BEAAA-274 §5 item 2, plan rev `54f086ca`.
**Contracts referenced:** `contracts/are-scanner-api-v1.md` (unchanged — this ADR does not modify the Scanner→Underwriter contract).

## Decision

Add an env-var-gated outbound-proxy adapter at `harness/egress.py` and route every existing scanner-host httpx call site through it. Default OFF (byte-equivalent to bare httpx). Enable per-scan by setting `BEAAA_RESI_PROXY_URL`, with optional rotation modes via `BEAAA_RESI_PROXY_ROTATE`.

## Reasoning

Path B scans (external probes of public AI surfaces for calibration / competitive / Spot-Scan) are increasingly walled by vendor-edge defenses: cloud-IP refusal (Bland.ai, Casetext, Perplexity, You.com, Lexis+, Consensus) and CF-WAF adaptive flag (ChatLab) — six wall families catalogued on BEAAA-274. Datacenter-ASN traffic is structurally distinguishable from end-user traffic; without a residential exit, ~half of our scan-target population is unreachable from the Paperclip host.

The adapter solves this without bleeding into scoring math. Single env-var enables; single env-var rolls back. No rubric, scanner_version, corpus_version, or seed semantics change — AC #5 preserved.

## Tradeoffs considered

- **Provider-specific SDK** (e.g., `brightdata-py`): rejected. Locks the codebase to one vendor; switching costs are non-trivial.
- **Global `HTTP_PROXY` / `HTTPS_PROXY` env vars**: rejected as the sole mechanism. Affects EVERY outbound httpx call including OpenRouter / Anthropic / Gemini judge calls, which we explicitly do NOT want to route through residential exits (a) for cost and (b) for cleanly distinguishing scan egress from judge egress in logs. The adapter keeps the gate scoped to scanner-host probes.
- **Per-call proxy kwarg threaded everywhere**: rejected. ~5 call sites in `harness/runner.py` + N scripts; threading a proxy URL kwarg through each is busywork. A shared module collapses this to a one-line `from harness import egress`.

## Rollback

Unset `BEAAA_RESI_PROXY_URL`. The adapter detects the missing env var and returns bare httpx behavior. Verified by `tests/test_egress.py::test_default_off_returns_none` and `test_client_passthrough_when_disabled`.

## Calibration / reproducibility (AC #5)

The adapter is infra, not scoring math. Calibration delta on the golden set must remain within ±1 point on total and every subscore (N=5 runs) with the proxy enabled. Verification gates the keep/discard decision in the POC report. If any golden moves > 1 point, the proxy is discarded for calibration runs regardless of wall-defeat efficacy.

## Tamper-resistance posture

- Proxy creds live in `.env` and are NOT logged, surfaced in score artifacts, or echoed into evidence trails.
- Scanner-input strings cannot influence proxy URL selection (no string interpolation into `proxy_url` flow).
- Per-session sticky IDs are derived from `secrets.token_hex(8)` or caller-supplied conversation IDs, NOT from prompt content.
- The adapter is transport-only; it does not parse vendor responses or alter scoring inputs.

## Pre-submission self-audit (per cto-os.md)

1. Single thing: ship infra that defeats wall families #1 and #2 on Path B without touching scoring math. ✓
2. Audience: CTO future self + Auditor (governance). Next CTO action: run wall-regress probe with creds (when delivered) and re-verify golden-set calibration band. ✓
3. Supreme Law respected: reproducibility band guarded (AC #5 verification gates keep), evidence trail unaffected, calibration discipline preserved, tamper resistance preserved, discrimination metric unaffected. ✓
4. `scanner_version` / `corpus_version` / `model_versions` / `seed`: not changed by this ADR. Calibration N=5 attaches when proxy is enabled in CI. ✓
5. Contract change: no. `contracts/are-scanner-api-v1.md` unchanged. ✓
6. Rollback plan: env-var unset, two lines of CI doc. ✓
7. Judge models pinned: N/A — no LLM judges added. ✓
8. Glossary check: ARE not BMI; IFA not BEAI. Repo directory `are-scanner/` per BEAAA-510 rename (ADR-0008). ✓
9. Carrier Rule: N/A. Internal infra. ✓
10. CEO / UW pre-empt: addressed — POC keeps/discards based on AC #4 report; recurring spend is a separate ask if approved. ✓
