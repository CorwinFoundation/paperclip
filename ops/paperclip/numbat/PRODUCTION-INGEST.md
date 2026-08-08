# Production appliance ingest

BLUF: this internal service wraps the sealed 0.1.1 verifier and writes only
counts, approved identifier hashes, and batch-chain hashes to Log Tracker.

Deployment is gated on exact-candidate QA in BEAAA-20237. Install the sealed
artifact with archive SHA-256
`a9321967886eed706fea4efaad67709e6caafee811a8bc86f632c757471e0665` at
`/opt/backbond/appliance-0.1.1`, install the Log Tracker package/migrations, and
enable `backbond-appliance-ingest.service`. The enrollment JSON contains
`appliance_id`, `tenant_id`, `source_id`, `public_key_pem`, and `token_env`; the
token value stays in the root-readable environment file.

Health: `GET /healthz`. Aggregate counts only: `GET /metrics`. Ingest uses
`POST /ingest` with bearer token plus `X-BackBond-Tenant` and
`X-BackBond-Source`. A 503 is retryable and never advances chain/idempotency
state. A 422 is a deterministic rejection.

Rollback: stop and disable this service. Queue/reject appliance delivery while
it is unavailable; do not route traffic to the test receiver or superseded
0.1.0 tenant-store implementation. The append-only receipt database remains
intact for audit and may be reopened after the adapter is restored.
