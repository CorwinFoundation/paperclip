# ADR-0071: Collector–Receiver Canonical JSON Parity

```yaml
owner: Scanner Engineer #3
artifact_type: decision
status: draft
updated_at: 2026-08-25
source_refs: [BEAAA-26068, BEAAA-26077, are-scanner-api-v1@v1.3.0, tools/are-collector/testdata/canonical_json_adversarial_events.json]
claim_or_decision: Go collector batch hashes use the same compact UTF-8 JSON bytes as the Python receiver.
verification: Go and Python execute the shared adversarial corpus; exact integer and HTML-significant bytes are asserted.
approvals: []
version: v0.1
```

## Decision

`batch_sha256` is SHA-256 over the UTF-8 bytes of compact JSON for the event
array, with lexicographically sorted object keys, array order preserved,
non-ASCII emitted directly, and HTML escaping disabled. This matches the
receiver's `canonical_dumps` implementation.

The Go implementation canonicalizes typed `schemav1.Event` values directly.
It does not marshal and unmarshal through `any`, because that converts JSON
integers to `float64` and cannot represent `9007199254740993` exactly.

## Evidence

`tools/are-collector/testdata/canonical_json_adversarial_events.json` is the
shared corpus. It covers `<`, `>`, `&`, Unicode, embedded HTML, URL query
strings, nested objects and arrays, and the integer `9007199254740993`.

The Go collector test asserts no HTML escaping and exact integer preservation.
The receiver test signs every corpus event with the Go collector and verifies
the resulting batch through Python receiver validation. The test also retains
the existing altered-payload rejection suite.

## Compatibility and rollback

This changes only the Passive Monitor transport hash preimage. Scanner version,
corpus version, score behavior, rubric, and public scoring envelope remain
unchanged under `are-scanner-api-v1@v1.3.0`. Revert the candidate commit as a
single unit; do not roll back only one side of the wire.
