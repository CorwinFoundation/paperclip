# D1 edge front (Caddy) — staging

**BEAAA-11866** · Owner: Scanner Engineer · Parent: BEAAA-11862 (CTO build) ·
CEO infra decision: BEAAA-11861 · Contract: `are-scanner-api-v1` **v1.1.0**
(no contract change — edge/infra, rule-execution).

**STAGING / non-public only.** No DNS, no `api.backbond.ai`, no publish. CEO owns
D2 (public bind + DNS) at go-time. This tier is **additive**: the scanner app
tier (`api/app.py`) is unchanged; rollback = tear the edge down.

## What it does

A Caddy reverse proxy in front of the scanner app enforcing three properties
(reusing the D-3 default-deny posture, BEAAA-11607):

1. **Route allowlist (default-deny).** Only these reach upstream:
   - `POST /scanner/v1/scans`
   - `GET /scan/{scan_id}` (exactly one path segment)

   Everything else — `/score`, `/quote`, `/forensic_bundle/*`,
   `/scanner/v1/rescans`, `/scanner/v1/sla`, portal/admin/health-detail,
   unknown paths, and wrong-method hits on the two allowed paths — returns an
   opaque `404 {"error":"not_found","request_id":<opaque>}` and never touches
   upstream.
2. **Topology hiding.** `Server`, `Via`, `X-Powered-By` (and `X-AspNet-Version`,
   `X-Runtime`) are stripped from every response; the upstream `Location` header
   is stripped on proxied responses. No internal host/port/path or stack trace
   leaves the edge.
3. **Error normalization.** Any upstream `5xx` becomes an opaque
   `502 {"error":"upstream_error","request_id":<opaque>}`; internal detail never
   passes through. Every response carries an opaque `X-Request-ID` header for
   support correlation (same id echoed in the error/404 bodies).

## Files

- `Caddyfile` — the edge config (validated + `caddy fmt`-clean against Caddy 2.8.4).
- `docker-compose.staging.yml` — staging deploy manifest, **loopback-only** publish.
- `README.md` — this file.

## Deploy (staging host)

```sh
# App tier already serving on the staging host (default :8077).
EDGE_UPSTREAM=host.docker.internal:8077 \
  docker compose -f docker-compose.staging.yml up -d
# Edge is now reachable only on 127.0.0.1:8080 of the staging host.
```

Rollback:

```sh
docker compose -f docker-compose.staging.yml down
```

### Config knobs (env)

| Var                 | Default                     | Meaning                                   |
| ------------------- | --------------------------- | ----------------------------------------- |
| `EDGE_LISTEN`       | `:8080`                     | Edge bind inside the container.           |
| `EDGE_UPSTREAM`     | `host.docker.internal:8077` | Scanner app tier `host:port`.             |
| `EDGE_PUBLISH_PORT` | `8080`                      | Loopback host port the edge publishes to. |

## Verify

Config validates and formats clean:

```sh
docker run --rm -v "$PWD/Caddyfile:/etc/caddy/Caddyfile:ro" \
  caddy:2.8-alpine caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

Behavior is proven by the integration test
`tests/integration/test_edge_caddy_staging.py`, which stands the edge up in
Docker against a fake upstream and asserts: (a) only the two routes reachable,
all others opaque-404; (b) no `Server`/`Via`/`X-Powered-By`/`Location`/
internal-path string in any header or body; (c) injected upstream 500 →
opaque `upstream_error` with no leaked detail.

```sh
python -m pytest tests/integration/test_edge_caddy_staging.py -v
```

The test skips cleanly if Docker is unavailable.
