# Scanner–Operations callback contract (`scanner-operations-contract@1`)

Every terminal callback from Scanner to Operations uses the same wire contract,
regardless of scan mode, terminal status, or delivery implementation. This
includes live-probe outbox deliveries and `operations_scope` full-scan worker
deliveries, including failure-replacement results with no `ScanResult` rows.

The POST request MUST contain exactly these contract headers (HTTP header names
remain case-insensitive on the wire):

- `X-BackBond-Signature: sha256=<hex HMAC-SHA256(secret, canonical_json(body))>`
- `X-BackBond-Delivery: <delivery identifier>`
- `Content-Type: application/json`

The signature covers the RFC 8785 canonical JSON bytes sent as the request
body. Secret resolution fails closed: Scanner does not send an unsigned
callback. Operations may reject a request before its handler when either
BackBond header is absent or when the content type is not JSON.

Contract fixtures:

- `tests/test_webhook_outbox_beaaa14400.py::test_delivers_signed_payload_exactly_once`
  checks the live-probe outbox header set and signature.
- `tests/test_full_scan_queue_worker_beaaa14843.py::test_invalid_persisted_scope_fails_only_that_job_and_worker_continues`
  checks the full-scan failure-replacement callback header set.

Both producers delegate signing and canonical-body construction to
`api.ci_scan_ext.build_signed_delivery`; producer-specific code must not build
or weaken this header set.
