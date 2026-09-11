# Datadog and Langfuse collector runbook

BEAAA-31860. This runbook operates the non-scoring source drivers in `tools/are-collector`. The drivers emit signed post-bind evidence batches and do not change the BackBond Score. Contract: `are-scanner-api-v1@v1.0`.

## Common invocation

Create the checkpoint directory with mode `0700`; the collector writes the file atomically with mode `0600`. Keep the Ed25519 private key in the runtime secret store and pass its base64 value through `ED25519_PRIVATE_KEY_B64`. Never put credentials in arguments recorded by a shell history or in committed files.

```sh
install -d -m 0700 /var/lib/are-collector
go run ./cmd/are-collector \
  -mode source-driver -source SOURCE \
  -customer-id CUSTOMER -agent-id AGENT -batch-id BATCH \
  -key-id KEY_ID -ed25519-private-key-b64 "$ED25519_PRIVATE_KEY_B64" \
  -checkpoint-path /var/lib/are-collector/checkpoint.json
```

The process writes a signed batch to stdout and errors to stderr. Send stdout to the local receiver as a separate step. Do not combine batch generation and delivery in a pipeline that can hide either exit status.

## Datadog

Set `DATADOG_SITE`, `DD_API_KEY`, `DD_APP_KEY`, and optionally `DATADOG_QUERY`. `DATADOG_SITE=datadoghq.com` is valid and becomes `https://api.datadoghq.com`; a full HTTPS API origin is also accepted. A path, user information, query string, fragment, or HTTP origin is rejected. The same values may be passed as `-datadog-site`, `-datadog-api-key`, `-datadog-app-key`, and `-datadog-query`.

Use a Datadog API key plus an application key scoped to log search. To rotate, create the replacement keys, update the runtime secret, run one window successfully, then revoke the old keys. Revocation during a window returns `datadog authorization denied (401)` or `(403)` and the runtime health is `auth_denied`. The batch is refused and the durable checkpoint is not changed. Restart with replacement keys and the same checkpoint path.

On a cold start, an empty Datadog cursor sets `filter.from` to `now-15m`. A saved cursor contains base64url-encoded JSON. `after_ts` is the latest delivered RFC3339 timestamp; `after_ids` contains every delivered event ID at that timestamp so boundary events are deduplicated without losing new events at the same instant. `page` and `window_to` are temporary pagination fields. To recover, restore the checkpoint file from backup and rerun. Delete only the `datadog` entry when a deliberate 15-minute cold start is acceptable; archive the original file first.

The Datadog allowlist admits the top-level `service`, `status`, `host`, and these nested attributes: `event_type`, `session_id`, `agent_id`, `deployment_id`, `operation_name`, `action_class`, `outcome_status`, `trace_id`, and `span_id`. Message bodies, prompts, outputs, transcripts, tool arguments, secrets, PII, and customer identifiers are excluded. There is no flag that enables them.

## Langfuse

Set `LANGFUSE_BASE_URL`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_VERSION=4.32.0`, or use the matching `-langfuse-*` flags. The base URL must be HTTPS; HTTP is accepted only for loopback testing. Credentials must be scoped to the intended Langfuse project.

To rotate, create a new scoped key pair, update both runtime secret values together, run one window successfully, then revoke the old pair. Revocation during a window returns `auth_denied: langfuse rejected scoped credentials`; runtime health is `auth_denied`, no batch is accepted, and the checkpoint remains byte-identical. Restart with the new pair and the same checkpoint path.

The Langfuse checkpoint separately tracks observation and score cursors plus completion flags. Restore the checkpoint file and rerun after a failure. Deleting the `langfuse` entry deliberately starts both streams from their API-defined beginning, so archive the original checkpoint and expect duplicates downstream.

The observations request asks only for `core,basic,time`; the scores request asks only for `core`. Normalized output admits record kind, type, name, level, source, data type, trace ID, observation ID, session ID, timestamps, and scalar score values. Prompts, inputs, outputs, transcripts, metadata, comments, tool arguments, secrets, PII, and customer identifiers are excluded. There is no flag that enables them.

## Source health

The runtime assigns exactly six states. The current one-shot CLI exposes failures on stderr and success as a signed batch on stdout; health names are available to embedded callers through `WindowResult.Health`.

| State | Operator-visible result |
| --- | --- |
| `connected` | Exit 0 with a signed batch containing one or more events; checkpoint advances. |
| `empty` | Exit 0 with a signed zero-event batch; checkpoint does not advance. |
| `stalled` | Non-zero exit after transient failures exhaust retries, or a non-empty page repeats its cursor; preserve the checkpoint and retry later. |
| `auth_denied` | Non-zero exit with Datadog 401/403 or Langfuse `auth_denied`; replace or restore scoped credentials. |
| `rate_limited` | Non-zero exit after 429 retries are exhausted; preserve the checkpoint and retry after the provider limit clears. |
| `schema_incompatible` | Non-zero exit for malformed provider data, an invalid checkpoint, redirect, or unsupported Langfuse version; preserve the checkpoint and correct the version/schema condition. |

## Receiver and rollback

The local receiver compose file requires a JSON keyring, not a single public-key flag:

```sh
export BACKBOND_MONITOR_JWT_KEYRING_JSON='{"KEY_ID":"BASE64URL_ED25519_PUBLIC_KEY"}'
docker compose -f tools/post-bind-ingest/docker-compose.local.yml up --build --abort-on-container-exit
```

Rollback is `docker compose -f tools/post-bind-ingest/docker-compose.local.yml down`. With the receiver down, batch delivery must fail non-zero. Keep the signed batch and checkpoint unchanged, restore the listener, then resend the same batch; never discard it or advance the checkpoint by hand.

`ingest.backbond.ai` has no DNS record and is not a deployed receiver. The Kubernetes sandbox therefore does not permit that hostname. Its source egress currently permits only `api.datadoghq.com:443`; add a BackBond receiver hostname only after DNS and the receiver are live. Langfuse deployments must add their exact HTTPS host to the allowlist before enabling that source.
