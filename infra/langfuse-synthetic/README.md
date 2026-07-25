# BEAAA-17702 bounded Langfuse synthetic backup

BLUF: this stack provides a BackBond-controlled, non-production Langfuse project for synthetic traces only. It is an observability sidecar and is never the authoritative BackBond Scanner evidence store or score path.

## Controls

- All seven images are pinned by immutable manifest digest.
- Only the synthetic ingress guard binds the host, on loopback `127.0.0.1:3300`; Langfuse, databases, and object storage remain on a Docker internal network.
- Upstream telemetry is disabled. Batch export is disabled.
- Project identity is fixed as `beaaa-17702`; the data window ends `2026-07-29T23:59:59Z`.
- No customer or production data is permitted. The ingress guard returns HTTP 422 for prohibited JSON keys (`email`, `phone`, `customer_id`, `customer_name`, `pii`) before forwarding ingestion to Langfuse. `smoke.sh` proves this service-side rejection and asserts the synthetic classification on retrieval.
- The synthetic trace carries and re-reads a SHA-256 evidence pointer. JSON/PDF rendering remains in the separately QA-approved BEAAA-17385 journey; this sidecar supplies trace, feedback, and hashed-evidence inputs without becoming its evidence store.
- No score, rubric, corpus, model, Scanner API, warranty, quote, bind, price, limit, decline, or insurance-decision behavior is present.

## Secret handling

Create the keys named in `required-secret-refs.json` in the Paperclip company secret provider. Do not paste values into git, shell history, issue comments, or receipts. Materialize the values into a root-owned mode `0400` or `0600` environment file only for Compose execution; disclose only Paperclip secret IDs on BEAAA-17702 and BEAAA-17703.

Map secret keys to environment variables:

| Paperclip key suffix | Environment variable |
| --- | --- |
| `nextauth-url` | `NEXTAUTH_URL` |
| `nextauth-secret` | `NEXTAUTH_SECRET` |
| `salt` | `SALT` |
| `encryption-key` | `ENCRYPTION_KEY` |
| `postgres-password` | `POSTGRES_PASSWORD` |
| `clickhouse-password` | `CLICKHOUSE_PASSWORD` |
| `redis-auth` | `REDIS_AUTH` |
| `minio-user` | `MINIO_ROOT_USER` |
| `minio-password` | `MINIO_ROOT_PASSWORD` |
| `langfuse-public-key` | `LANGFUSE_INIT_PROJECT_PUBLIC_KEY` |
| `langfuse-secret-key` | `LANGFUSE_INIT_PROJECT_SECRET_KEY` |
| `operator-email` | `LANGFUSE_INIT_USER_EMAIL` |
| `operator-password` | `LANGFUSE_INIT_USER_PASSWORD` |

## Candidate lifecycle

Deployment is prohibited until QA Engineer approval on the exact commit and digest set. After approval, install `Caddyfile.fragment` in the existing box Caddy configuration, set `LANGFUSE_SYNTHETIC_HOST`, validate Caddy, and run:

```sh
LANGFUSE_ENV_FILE=/run/secrets/beaaa-17702.env ./infra/langfuse-synthetic/deploy.sh
```

Acceptance from a clean client, after resolving only the scoped project key secret references:

```sh
LANGFUSE_BASE_URL=https://synthetic-host.example \
LANGFUSE_PUBLIC_KEY="$SCOPED_PUBLIC_KEY" \
LANGFUSE_SECRET_KEY="$SCOPED_SECRET_KEY" \
./infra/langfuse-synthetic/smoke.sh
```

Teardown at or before `2026-07-29T23:59:59Z`:

```sh
LANGFUSE_ENV_FILE=/run/secrets/beaaa-17702.env \
./infra/langfuse-synthetic/teardown.sh /var/lib/backbond/receipts/beaaa-17702-teardown.json
```

Then revoke every associated Paperclip secret ID and delete the materialized environment file. The teardown removes only the dedicated `backbond-langfuse-synthetic` Compose project and its volumes; it does not touch the Scanner deployment.

## Scanner metadata

- Contract: `are-scanner-api-v1@v1.0`
- Classification: rule execution, "customer-side harness" / "smoke test for an existing operation"
- `scanner_version`: inherited from separately QA-approved BEAAA-17385 candidate; unchanged
- `corpus_version`: inherited from separately QA-approved BEAAA-17385 candidate; unchanged
- `probe-set-version`: inherited from separately QA-approved BEAAA-17385 candidate; unchanged
- `rubric_point_id`: none; observability sidecar only
- N=5 calibration delta: zero by construction; no Scanner scoring path is touched

FAL: Focus passes because the stack supports continuous scanner evidence. Alignment passes because it is explicitly non-authoritative and synthetic-only. Linkage passes through the shared proof point "continuous scanner evidence."
