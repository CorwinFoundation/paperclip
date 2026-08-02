# ADR-0064: Direct vendor-local OTel source driver

BLUF: BEAAA-20418 adds a pinned OTLP-only source adapter inside `tools/post-bind-ingest`; it admits an explicit attribute allowlist and returns canonical evidence only after whole-batch validation.

- Status: candidate implementation, rule-execution
- Contract: `contracts/are-scanner-api-v1.md@v1.3.0`
- Collector: upstream core builder/components `v0.157.0`
- Source driver: `otel-collector-direct-v1`
- Evidence tier: `vendor-local-redacted`

## Decision

The Collector binds OTLP gRPC/HTTP to loopback and has only a no-op exporter. The Python adapter validates the OTLP JSON boundary, rejects unknown fields and attributes, rejects content by default, detects secret-bearing admitted values, enforces clock and monotonic sequence constraints, and emits no partial batch. Existing receiver signing and hash-chain code consumes only the adapter's returned canonical events.

## Threat model

The trust boundary is hostile telemetry arriving inside the vendor environment. Replay, reorder, malformed identifiers, clock skew, schema smuggling, unknown attributes, content capture, and credential-shaped values terminate the batch before sequence state advances. The adapter does not score, protect, transmit externally, or enter an application request path.

## Build evidence

`otelcol-builder.yaml` is the component manifest and minimal SBOM input. `collector.yaml` is the effective configuration input. Candidate build digest and effective-config SHA-256 are generated after the exact candidate is committed; no promotion is authorized without QA approval on that SHA.

## Operational metadata

- scanner_version: unchanged
- corpus_version: unchanged
- probe-set-version: unchanged
- rubric_point_id: `OPS.DATA_HANDLING.LOCAL_INGEST`
- N=5 calibration delta: pending candidate verification; expected 0 because no score path is imported
- cto_cosign_ref: not required under the rule-execution classification recorded on BEAAA-20418

FAL: internal engineering decision. Focus and linkage are satisfied by extending the existing Passive Monitor runtime-evidence lane; no external claim is introduced.
