# agent-env-audit

Detects any `claude_local` agent whose `adapterConfig.env` carries
`ANTHROPIC_API_KEY`. That override silently bypasses the host Max OAuth and
routes traffic to Anthropic Console PAYG. Source: **BEAAA-5323** (Founder asked
for guardrails after SE1 burned $37.66 against the API while Max sat idle).

## Run

```
python3 tools/agent-env-audit/audit.py
```

Writes a timestamped markdown report to `reports/`. Exit codes:

- `0` clean
- `1` leak detected (registry or `/proc`)
- `2` cannot reach Paperclip API

The script reads `PAPERCLIP_API_URL` and `PAPERCLIP_COMPANY_ID` from env, with
sensible defaults for this VM.

## What it checks

1. **Registry scan** — `GET /api/companies/{cid}/agents`, filters
   `adapterType=claude_local`, flags any `adapterConfig.env.ANTHROPIC_API_KEY`.
2. **/proc scan** — walks live `claude` processes on this VM, reports which
   have `ANTHROPIC_API_KEY` set in env. Catches the case where the registry is
   clean but a stale process is still bleeding to PAYG.

## Remediation

```
curl -X PATCH "$PAPERCLIP_API_URL/api/agents/<id>" \
  -H 'Content-Type: application/json' \
  -d '{"adapterConfig":{"env":{"<keep_key>": ... }}}'
```

`PATCH env` does **full replace** — include every key you want kept. Drop only
`ANTHROPIC_API_KEY`. Verify with a follow-up `GET`. The current run will keep
the old env baked in until it exits; the next wake inherits the host Max OAuth.

## Daily routine

A Paperclip routine at `routines/<id>` runs this audit daily and files a child
ticket on **BEAAA-5323** when leak is detected. If the audit is clean it logs
and exits — no ticket spam.

## Provenance

Restored for **BEAAA-16376** from the implementation shipped by **BEAAA-7315**.
The BEAAA-7315 closeout comment (2026-06-22 06:47:26Z) records this exact
tool path, exit contract, `claude_local` registry scan, and stale `/proc`
process scan. BEAAA-16376 added deterministic tests and the
`PAPERCLIP_AGENT_ENV_AUDIT_PROC_DIR` test override; the runtime default remains
`/proc`.

## Test

```
python3 tools/agent-env-audit/test_audit.py
```
