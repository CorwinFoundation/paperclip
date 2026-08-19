# Production appliance ingest

BLUF: this internal service wraps the sealed 0.1.0 verifier and writes only
counts, approved identifier hashes, and batch-chain hashes to Log Tracker.

Deployment is gated on exact-candidate QA in BEAAA-20237. Install the sealed
artifact `ops/paperclip/numbat/backbond-appliance-0.1.0.zip`, archive SHA-256
`dbef6e73a715adda40e7697c070a96b7844398e09c7925926b8e541e5c87ef8a`, at
`/opt/backbond/appliance-0.1.0`, install the Log Tracker package/migrations, and
enable `backbond-appliance-ingest.service`.

That SHA is reproducible from the repository:
`sha256sum ops/paperclip/numbat/backbond-appliance-0.1.0.zip`. It also appears as
`artifact.archiveSha256` in `backbond-appliance-0.1.0.provenance.json`, whose
`sourceManifestSha256` covers the 39-file unpacked tree. BEAAA-20237 F4 recorded
that the previously pinned 0.1.1 archive SHA existed nowhere but that one doc
line: no 0.1.1 tree exists in the repository or on the host, so the deployment
was not reproducible. 0.1.0 is the only sealed appliance that ships here, and it
is the version this adapter is tested against. The enrollment JSON contains
`appliance_id`, `tenant_id`, `source_id`, `public_key_pem`, and `token_env`; the
token value stays in the root-readable environment file.

Health: `GET /healthz`. Counts only: `GET /metrics`, which requires the same
bearer token and `X-BackBond-Tenant` header as ingest and returns that tenant's
counts alone; an unauthenticated or cross-tenant read is 403. Ingest uses
`POST /ingest` with bearer token plus `X-BackBond-Tenant` and
`X-BackBond-Source`. A 503 is retryable and never advances chain/idempotency
state. A 422 is a deterministic rejection.

Rollback: stop and disable this service. Queue/reject appliance delivery while
it is unavailable; do not route traffic to the test receiver or the superseded
tenant-store implementation. The append-only receipt database remains
intact for audit and may be reopened after the adapter is restored.
