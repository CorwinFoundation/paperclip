# Sequence 20 external edge — nginx

The deployed **nginx** external edge for the BackBond Scanner (Sequence 20). It
mirrors the posture of the neighbouring staging `infra/edge/Caddyfile`
(BEAAA-11866) on the edge the box actually runs, and closes gap 1 of the Founder
Stage Gate FAIL (BEAAA-16216, attachment `d05e00d8`): the deployed nginx returned
**no `X-Request-ID`** on ordinary responses, so responses could not be correlated
to access-log lines.

## Topology correctness (BEAAA-16331 — why there are two files)

Production `scanner.backbond.ai` is served by **exactly one** TLS vhost at
`/etc/nginx/sites-available/scanner.backbond.ai` (symlinked from
`/etc/nginx/sites-enabled/`). The earlier deployable surface was a single
**standalone `server {}` block** with its own `listen ${EDGE_LISTEN}` and
`server_name _`. Installed live, that would have created a **second, parallel /
conflicting TLS listener** next to the real vhost. The Sequence 20 operator's
fail-closed pre-mutation inventory caught this before any change (BEAAA-16232
stop `962f9417`).

The topology-correct surface adds **no server block and no listener**. It is two
fragments:

| File | nginx scope | Where it is installed |
|---|---|---|
| `backbond-scanner-edge.http.conf` | **http** | dropped into `/etc/nginx/conf.d/` — declares only the `edge_json` `log_format` |
| `backbond-scanner-edge.server.conf.template` | **server** | `include`d **inside** the existing `scanner.backbond.ai` `server {}` block, replacing its scanner proxy location(s) |

Neither fragment carries `listen`, `server_name`, `ssl_certificate`, or a
`server {}` wrapper, so the surface cannot create, duplicate, or shadow the live
TLS listener. `nginx` **rejects** the server fragment if it is (mis)used at
top/http scope — see the topology test below.

## What the surface guarantees (additive; unchanged posture)

- `X-Request-ID: $request_id` is added with the **`always`** flag, so ordinary
  `401` / `404` / `5xx` responses now carry an opaque per-request id.
- A JSON `access_log` (`edge_json`) records the **same** `$request_id`, giving
  exact response↔log correlation (not timestamp-only). The `log_format` is the
  http fragment; the `access_log` that uses it is the server fragment.
- `$request_id` is also forwarded upstream (`proxy_set_header X-Request-ID`) so
  the app-tier log line aligns with the edge line.
- Route allowlist / default-deny, topology hiding (`server_tokens off` +
  `proxy_hide_header` for Server/Via/X-Powered-By/Location/X-Runtime/X-AspNet-Version),
  and upstream-5xx → opaque `upstream_error` are **preserved** unchanged.

No score / API contract / rubric / corpus behaviour changes.

## Rendering

Only `${EDGE_UPSTREAM}` is substituted in the server fragment (the http fragment
has no placeholders). Keep nginx runtime vars (`$request_id`, …) literal, e.g.:

```
envsubst '${EDGE_UPSTREAM}' \
  < backbond-scanner-edge.server.conf.template \
  > /etc/nginx/backbond-scanner-edge.server.conf
```

Set `EDGE_UPSTREAM` to the app-tier `host:port` the **live vhost already proxies
to** (captured in the backup step of the runbook). Do not invent a new upstream.

## Test

- `tests/integration/test_edge_nginx_topology_beaaa16331.py` — static, always
  runs. Reproduces the parallel-listener defect (the deployable server fragment
  must carry no `listen`/`server_name`/`server {}`), proves the legacy standalone
  template is retired, and (when `nginx` is available) checks the fragments
  validate inside a synthesized vhost and are rejected at http scope.
- `tests/integration/test_edge_nginx_request_id_beaaa16230.py` — composes the two
  fragments in Docker (http fragment at http scope + a `server { listen; include
  <server fragment>; }`) in front of an auth-aware fake upstream and proves the
  ordinary 401/404 header, exact log correlation, and preserved redaction. Skips
  cleanly when Docker is unavailable.

## Rollback

The server fragment mutates only the live vhost's location set. Rollback =
restore the **exact** prior `/etc/nginx/sites-available/scanner.backbond.ai` file
and its `sites-enabled` symlink, plus the Sequence 20 image
`backbond-scanner-live@sha256:7c4ff251dd0cc4d41b88e9e148094355985dc4a39e7ac4e93e280171c4101121`.
**Do not promote from this ticket** — hand the candidate SHA to QA for fresh
APPROVED, and production/public promotion additionally requires fresh Founder
sign-off under BEAAA-15373.
