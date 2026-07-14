# Real 203-case agent demo

This is the execution-first demo. It sends 203 target-bound registry cases to one authenticated agent and writes a signed per-case evidence manifest. It does not use fixture responses or manufacture an aggregate score.

One run executes:

| Execution set | Cases | Target calls | Maximum judge calls |
| --- | ---: | ---: | ---: |
| Original golden families | 90 | 110 | 90 |
| Added text-compatible cases | 113 | 136 | 113 |
| **Total** | **203** | **246** | **203** |

The added 113 cases are materialized and dispatched through the common authenticated adapter, with evidence tagged to the registered Promptfoo (71), Garak (27), Inspect (10), and PyRIT (5) worker definitions. The 90 original cases cover factuality, consistency under paraphrase, refusal accuracy, and confidence calibration. The expanded set spans 24 registry families.

## Evidence boundary

The registry deliberately distinguishes runnable cases from cases that already have recorded execution evidence:

- `target_bound=true` means the demo can materialize the case, call the configured target, evaluate the response, and write evidence. The demo is pinned to 203 such cases.
- `production_runner_executable=true` means the repository already contains the concrete action, expected oracle, recorded target response, verdict, and GitHub provenance required by the current evidence gate.

The published executed count therefore remains evidence-gated (currently 1) until real execution records are reviewed and committed. Adding the 113 runnable cases does not inflate that historical claim.

The service uses `tools.active_test_registry`, `tools.text_case_materializer`, `tools.registered_case_executor`, and `api.full_scan_executor` directly. A behavioral failure is a completed case with verdict `fail`. Missing target/judge calls, budget exhaustion, or adapter errors produce an explicit incomplete analysis.

## Runtime configuration

Create `/etc/backbond/real-agent-demo.env` as uid `10001`, mode `600`. The deployed Max-backed judge uses:

```text
DEMO_ACCESS_TOKEN=<32+ random characters>
ARE_BUNDLE_SIGNING_KEY=<random signing key>
DEMO_JUDGE_BACKEND=claude-code-proxy
CLAUDE_JUDGE_PROXY_TOKEN=<32+ random characters>
CLAUDE_JUDGE_MODEL=sonnet
```

An API-backed deployment may use `OPENROUTER_API_KEY` or `ANTHROPIC_API_KEY` instead. The target defaults to the existing authenticated Syllable demo agent at `https://apichat.syllable.cloud`; its public Syllable headers are supplied by the repository's hardened customer runner. A non-Syllable target also requires `DEMO_AGENT_ENDPOINT` and `DEMO_AGENT_SECRET`.

For the Max backend, `install_claude_judge_proxy.sh` runs Claude Code on the host behind a mode-660 Unix socket. The public container receives only the socket and a dedicated bridge token; the Claude login is never mounted into Docker. Every judgment runs with safe mode, no tools, no slash commands, no persisted session, one permitted turn, an empty MCP configuration, and a $1 per-call nominal ceiling. The subprocess receives an allowlisted environment that excludes the demo access token, signing key, bridge token, and any API keys.

The secret file is bind-mounted read-only. Credential values do not appear in `docker inspect`, API payloads, logs, manifests, or case evidence.

The deployment fixes the authorization ceilings at 246 target calls, 203 judge calls, $100 estimated judge cost, and eight hours wall clock. The manifest retains the scanner's conservative estimated-cost ledger so API-backed and Max-backed runs use the same accounting. A persistent marker is written before the run starts, enforcing the approved initial budget as one total run even after container restarts. An operator must explicitly remove `/data/runs/.initial-evaluation-budget-consumed` to authorize a later evaluation.

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

Acceptance requires exactly `203 expected = 203 attempted = 203 terminal = 203 evidenced`, exactly 246 target calls, no more than 203 judge calls, and estimated judge cost at or below $100. A deterministic canary or secret leak can produce a terminal failure without spending a judge call.
