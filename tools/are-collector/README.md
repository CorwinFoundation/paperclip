# BackBond Collector

BEAAA-5967 implementation slice for ADR-0035 / BEAAA-5966.

This package is the customer-side collector runtime. Build #3 is fixture/local-smoke only: it reads JSON-lines fixtures through the common adapter interface, keeps only Log Event Schema v1.0 event families, applies source-side redaction, and produces signed egress batches for the BackBond receiver. Real Datadog/Splunk/CloudWatch/OTEL adapter wiring is intentionally not enabled in this image.

Boundary invariants:

- Contract: `are-scanner-api-v1@v1.0`
- Schema pin: `log-event-schema-v1.0`
- Scanner output delta: `0` by construction; this package has no scoring, rubric, pricing, portal, or detector imports.
- Supervision classification: rule-execution per `scanner-implementation-supervision-v1@v1.1`, row 17 and ADR-0035 §8(a)-(c).

Adapters:

- Datadog Logs API v2 search: `adapter.NewDatadogLogsAPI`
- Splunk REST export: `adapter.NewSplunkRESTExport`
- CloudWatch Logs `FilterLogEvents`: `adapter.NewCloudWatchFilterLogEvents`
- OTEL Collector OTLP gRPC receive: `adapter.NewOTELGRPCReceiver`
- Deterministic fixture mode for tests and smoke runs: `adapter.AdapterFromFixture`

Run:

```sh
go test ./...
```

Fixture smoke:

```sh
printf '{"event_type":"tool_call","event_id":"evt-1","logged_at":"2026-06-16T18:00:00Z","payload":{"args":{"email":"jane@example.com"}}}' |
  go run ./cmd/are-collector \
    -mode fixture-smoke \
    -source datadog \
    -customer-id customer-1 \
    -agent-id agent-1 \
    -batch-id batch-1 \
    -key-id fixture-bind-key \
    -ed25519-private-key-b64 "$ED25519_PRIVATE_KEY_B64" \
    -policy-json '{"args.email":"redact-on-receipt"}'
```

Release:

- `.github/workflows/are-collector-release.yml` builds the scratch-base image on `are-collector-v0.1.*` tags.
- The release workflow emits a Syft SPDX JSON SBOM artifact.
- The release workflow signs the image with keyless Cosign.
- Auditor verification command:

```sh
cosign verify \
  --certificate-identity-regexp '^https://github.com/CorwinFoundation/are-scanner/.github/workflows/are-collector-release.yml@refs/tags/are-collector-v0\.1\.[0-9]+$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/CorwinFoundation/are-scanner/are-collector:are-collector-v0.1.0
```

Sandbox:

- `deploy/kubernetes-sandbox.yaml` sets non-root execution, read-only root filesystem, dropped Linux capabilities, RuntimeDefault seccomp, and an egress-only CiliumNetworkPolicy pinned to `ingest.backbond.ai:443`.
