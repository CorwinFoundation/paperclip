# Sequence 20 external edge — nginx

`backbond-scanner-edge.conf.template` is the deployed **nginx** external edge for
the BackBond Scanner (Sequence 20). It mirrors the posture of the neighbouring
staging `infra/edge/Caddyfile` (BEAAA-11866) on the edge the box actually runs,
and closes gap 1 of the Founder Stage Gate FAIL (BEAAA-16216, attachment
`d05e00d8`): the deployed nginx returned **no `X-Request-ID`** on ordinary
responses, so responses could not be correlated to access-log lines.

## What changed vs. the pre-candidate edge (additive only)

- `X-Request-ID: $request_id` is added with the **`always`** flag, so ordinary
  `401` / `404` / `5xx` responses now carry an opaque per-request id.
- A JSON `access_log` (`edge_json`) records the **same** `$request_id`, giving
  exact response↔log correlation (not timestamp-only).
- `$request_id` is also forwarded upstream (`proxy_set_header X-Request-ID`) so
  the app-tier log line aligns with the edge line.
- Route allowlist / default-deny, topology hiding (`server_tokens off` +
  `proxy_hide_header` for Server/Via/X-Powered-By/Location/X-Runtime), and
  upstream-5xx → opaque `upstream_error` are **preserved** unchanged.

No score / API contract / rubric / corpus behaviour changes.

## Rendering

The stock nginx image entrypoint runs `envsubst` over
`/etc/nginx/templates/*.template`. Mount this file there and set:

```
EDGE_LISTEN=8080                     # edge bind (publish edge-only)
EDGE_UPSTREAM=127.0.0.1:8077         # scanner app tier host:port
NGINX_ENVSUBST_FILTER=EDGE           # only EDGE_* are substituted; nginx
                                     # runtime vars ($request_id, ...) stay literal
```

`NGINX_ENVSUBST_FILTER=EDGE` is **required** — without it `envsubst` would blank
out `$request_id` and the fix would silently disappear.

## Test

`tests/integration/test_edge_nginx_request_id_beaaa16230.py` stands this template
up in Docker in front of an auth-aware fake upstream and proves the ordinary
401/404 header, exact log correlation, and preserved redaction. A static lint in
the same file always runs (no Docker needed).

## Rollback

Additive edge config. Rollback = restore the current nginx config and the
Sequence 20 image
`backbond-scanner-live@sha256:7c4ff251dd0cc4d41b88e9e148094355985dc4a39e7ac4e93e280171c4101121`.
**Do not promote under BEAAA-16230** — hand the candidate SHA to QA.
