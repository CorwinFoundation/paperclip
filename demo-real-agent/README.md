# Real 90-case agent demo

This is the execution-first demo. It replaces the synthetic 80→81 fixture story with the production active registry and its signed evidence manifest.

One run executes:

| Family | Cases | Target calls | Judge calls |
| --- | ---: | ---: | ---: |
| Factuality golden set | 30 | 30 | 30 |
| Consistency under paraphrase | 20 | 40 | 20 |
| Refusal accuracy | 20 | 20 | 20 |
| Confidence calibration | 20 | 20 | 20 |
| **Total** | **90** | **110** | **90** |

The service uses `tools.active_test_registry`, `tools.registered_case_executor`, and `api.full_scan_executor` directly. A behavioral failure is a completed case with verdict `fail`. Missing target/judge calls, budget exhaustion, or adapter errors produce an explicit incomplete analysis.

The UI intentionally does not display the old fixture score or manufacture an aggregate score from four rubric families. The signed manifest and its per-case verdicts are the measured result.

## Runtime configuration

Create `/etc/backbond/real-agent-demo.env` as uid `10001`, mode `600`, with:

```text
DEMO_ACCESS_TOKEN=<32+ random characters>
ARE_BUNDLE_SIGNING_KEY=<random signing key>
OPENROUTER_API_KEY=<judge key>
```

`ANTHROPIC_API_KEY` may replace `OPENROUTER_API_KEY`. The target defaults to the existing authenticated Syllable demo agent at `https://apichat.syllable.cloud`; its public Syllable headers are supplied by the repository’s hardened customer runner. A non-Syllable target also requires `DEMO_AGENT_ENDPOINT` and `DEMO_AGENT_SECRET`.

The secret file is bind-mounted read-only. Credential values are loaded into the scanner process but do not appear in `docker inspect`, API payloads, logs, manifests, or case evidence.

The deployment fixes the authorization ceilings at 110 target calls, 90 judge calls, $100 estimated judge cost, and three hours wall clock. It also writes a persistent marker before the run starts. This enforces the approved initial budget as one total run, even after container restarts. An operator must explicitly remove `/data/runs/.initial-evaluation-budget-consumed` to authorize a later evaluation.

Share an access link using a URL fragment so the run token is not sent in the initial HTTP request or recorded by the tunnel:

```text
https://demo.example/#token=<DEMO_ACCESS_TOKEN>
```

## Acceptance

Health-only verification does not spend evaluation budget:

```bash
python3 demo-real-agent/smoke_test.py --url http://127.0.0.1:8765 --health-only
```

The post-deploy proof performs the one approved real run:

```bash
python3 demo-real-agent/smoke_test.py \
  --url http://127.0.0.1:8765 \
  --token "$DEMO_ACCESS_TOKEN" \
  --execute
```

Acceptance requires exactly `90 expected = 90 attempted = 90 terminal = 90 evidenced`, 110 target calls, 90 judge calls, and estimated judge cost at or below $100.
