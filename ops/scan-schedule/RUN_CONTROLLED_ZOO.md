# Controlled-zoo one-shot run

The scanner image contains the runner, controlled targets, configured corpus,
and runtime dependencies. After master deploys, an `are-deploy` SSH session can
run one zoo scan without a checkout or a second deployment path:

```sh
curl --fail --silent --show-error --max-time 21630 \
  --request POST http://127.0.0.1:8077/internal/ops/controlled-zoo/run
```

The command returns only after the manifest has been written beneath
`/var/lib/are-scanner/scheduled-scans/<UTC-date>/zoo/manifest.json`. That path is
the host-mounted scanner state volume. The response includes the manifest path
and the complete manifest, including per-family `considered`, `routed`,
`target_called`, `pass`, `fail`, and reason-coded `incomplete` counts, total
runtime, and estimated cost.

The route is absent from OpenAPI, is not allowed through the public nginx edge,
and rejects requests carrying `X-Forwarded-For`. It is supported only through
the loopback port available from an authenticated server session.
