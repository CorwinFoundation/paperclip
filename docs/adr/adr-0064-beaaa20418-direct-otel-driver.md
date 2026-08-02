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

`otelcol-builder.yaml` is the component manifest and minimal SBOM input. `collector.yaml` is the effective configuration input. The generated receipt records binary SHA-256 `f45eca18c6bf8efead8760de8512079173e272491a567e5463e32f794c8bfa84` and effective-config SHA-256 `1b1c2aefb3c62d5e21fa96a0ffe4ba7d3b486f6948fe6242678d2d6560c54142`. No promotion is authorized without QA approval on the exact candidate SHA.

## Operational metadata

- scanner_version: `0.5.0` (unchanged)
- corpus_version: `frozen-0.5.0` (unchanged)
- probe-set-version: `not-applicable-runtime-evidence-driver`
- rubric_point_id: `OPS.DATA_HANDLING.LOCAL_INGEST`
- N=5 calibration delta: `0`; five isolated integration runs passed and no score path is imported
- cto_cosign_ref: not required under the rule-execution classification recorded on BEAAA-20418

FAL: internal engineering decision. Focus and linkage are satisfied by extending the existing Passive Monitor runtime-evidence lane; no external claim is introduced.
