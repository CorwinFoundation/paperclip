# BackBond Scanner — CI/CD plugin

Gate your CI/CD pipeline on your agent's **BackBond Score**. One YAML block
runs a BackBond scan against your *staging* agent and breaks the build when
the score falls below your threshold — without ever reddening the build on a
BackBond outage, and refusing outright to scan production.

> **Internal-only.** This component is not published to the GitHub
> Marketplace or the GitLab CI/CD Catalog. External release is gated
> (BEAAA-11755 → CEO release gate + Founder confirmation on BEAAA-11710).
> It wraps the **shipped** scanner routes only and ships **no probe corpus**.

## GitHub Action

```yaml
- uses: backbond/are-scanner-action@v1        # internal: uses: ./ci_plugin
  with:
    agent_endpoint: https://staging-agent.example.com   # required, NOT production
    backbond_api_key: ${{ secrets.BACKBOND_KEY }}       # CI secret
    auth_token: ${{ secrets.AGENT_AUTH }}               # CI secret (optional)
    scan_tier: full                                     # smoke | full (default full)
    fail_below_score: 72                                # optional, stricter-only
```

## GitLab CI

```yaml
include:
  - local: 'ci_plugin/templates/.gitlab-ci.yml'

backbond_scan:
  extends: .backbond_scan
  variables:
    BACKBOND_AGENT_ENDPOINT: "https://staging-agent.example.com"
    BACKBOND_SCAN_TIER: "full"
    BACKBOND_FAIL_BELOW_SCORE: "72"
  # BACKBOND_BACKBOND_API_KEY / BACKBOND_AUTH_TOKEN as masked CI/CD variables
```

## What it does

1. **Preflight (§6).** Reads the agent's WS-2 self-identification. If the
   endpoint declares **production**, the plugin refuses and exits `3` — it
   never scans a live production system.
2. **Submit.** `POST /scanner/v1/scans` with `scan_tier` → `scan_id`.
3. **Poll.** `GET /scan/{scan_id}` with bounded backoff to a terminal score.
4. **Verify.** If a completion webhook is delivered, its **HMAC-SHA256**
   signature (`X-BackBond-Signature`) is verified before the score is
   trusted. A mismatch fails **closed** (exit `4`).
5. **Gate.** The threshold is your vendor policy band (looked up against
   your BackBond account); `fail_below_score` is a **stricter-only**
   override. Below → break the build.

## Exit codes

| Condition | Exit | Breaks build? |
|---|---|---|
| score ≥ threshold | `0` | no |
| score < threshold | `1` | **yes** |
| endpoint self-identifies production (§6) | `3` | yes (refuse) |
| webhook HMAC mismatch / tamper | `4` | yes (fail-closed) |
| infra error, retries exhausted | `75` | **no** — neutral advisory |
| smoke tier below threshold | `1` | yes, but **NON-bindable** |

- **Infra vs. score.** Transport/availability failures are retried with
  backoff; on exhaustion the plugin returns the neutral advisory `75`
  (`EX_TEMPFAIL`) and does **not** red your build. A BackBond outage is not
  a risk signal.
- **Smoke tier** is a fast, deterministic partial. It is **advisory only,
  never bindable**, and never feeds Underwriter pricing.

## Secrets

`backbond_api_key` and `auth_token` are read from CI secrets and are
**never** written to logs (every log line is redacted).

## Internal build & test

```bash
# Run the acceptance matrix (mock scanner harness — no network):
python -m pytest tests/test_ci_plugin_beaaa11729.py -q

# Build the container (context = repo root):
docker build -f ci_plugin/Dockerfile -t are-scanner-action:local .
```
