# BackBond Collector

BEAAA-5967 implementation slice for ADR-0035 / BEAAA-5966.

This package is the customer-side collector runtime. Build #3 is fixture/local-smoke only: it reads JSON-lines fixtures through the common adapter interface, keeps only Log Event Schema v1.0 event families, applies source-side redaction, and produces signed egress batches for the BackBond receiver. Real Datadog/Splunk/CloudWatch/OTEL adapter wiring is intentionally not enabled in this image.

Boundary invariants:

- Contract: `are-scanner-api-v1@v1.0`
- Schema pin: `log-event-schema-v1.0`
- Scanner output delta: `0` by construction; this package has no scoring, rubric, pricing, portal, or detector imports.
- Supervision classification: rule-execution per `scanner-implementation-supervision-v1@v1.1`, row 17 and ADR-0035 §8(a)-(c).

Collector v1 provenance (BEAAA-11365):

- Every batch carries a monotonic per-`(customer_id, agent)` `sequence` (starts at 1) inside the signed JWT claims, so missing batches are detectable at the receiver.
- `-heartbeat` emits a zero-event batch that still consumes a sequence number, so a silent agent that stops emitting real telemetry is still observable.
- `internal/verify` re-derives the batch content hash, verifies the collector Ed25519 signature, and enforces tenant isolation: a batch only verifies against keys registered for the `customer_id` it claims. Verification fails closed on tamper, foreign/unknown key, or claim/envelope drift.
- `internal/ledger` is the tenant-partitioned collector-side store that records the verified sequence stream and flags missing sequences (`OutstandingGaps`) and silent agents (`SilentAgents`).
- Per-span/event redaction status is recorded on each event as `redaction_applied`.

Rescan-on-change triggers (BEAAA-11374, spec §3.4.A / build item 7):

- `internal/triggers` derives operational drift from Evidence Profile v1 rows (`backbond.evidence.*`) and, on a positive trigger, appends a `RescanRequest` to an `AuditSpine`. Three classes, per `(tenant_id, agent_id, deployment_id)` stream:
  - `model_ref_change` — `model_ref` differs from the last observed value (model swap).
  - `authority_expansion` — a new tool or permission appears in `authority_context`.
  - `outcome_drift` — `outcome_status` failure/escalation/reversal rate over a rolling window crosses a configurable threshold.
- Triggers **request rescans only**. `RescanRequest` carries no score/band/rubric field and this package has no scoring imports (enforced by `tests/no_score_imports_test.go`), so a trigger can never move a BackBond Score directly. Consuming the emitted requests is a separately approved scanner release; `scanner_version` / `corpus_version` are unchanged.
- Thresholds are explicit and configurable via `triggers.Config` (`DefaultConfig()` supplies conservative defaults). `Config.Enabled` is the rollback gate: when `false`, evidence ingestion (baseline tracking) is unchanged but no request is emitted.
- `RequestID` is deterministic in the triggering evidence, so a replayed row yields the same id and a spine consumer can dedupe rather than double-request.

Adapters:

- Datadog Logs API v2 search: `adapter.NewDatadogLogsAPI`
- Splunk REST export: `adapter.NewSplunkRESTExport`
- CloudWatch Logs `FilterLogEvents`: `adapter.NewCloudWatchFilterLogEvents`
- OTEL Collector OTLP gRPC receive: `adapter.NewOTELGRPCReceiver`
- Deterministic fixture mode for tests and smoke runs: `adapter.AdapterFromFixture`
- Versioned sensor boundary: `internal/source.SourceDriver` (`source-driver-v0.1`)
- Numbat coding-agent source: `source.NumbatSourceDriver`, pinned to
  `3d20d782d45001fd3bb200bc5690ce4b9ce0f12b` with deterministic mapping
  `numbat-map-v0.1`. The driver only acquires and maps; Collector core owns
  fail-closed redaction, validation, batching, signing, retry, and checkpoint
  commit. Unknown kinds are retained as non-billable observability events.

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
