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

## P1 enrollment invariant (single-tenant appliance)

**In P1 an appliance_id is enrolled for exactly one tenant_id, for the life of
the enrollment row.** This is a named control, not an accident of the pilot's
shape. `ApplianceIngestStore.enroll(appliance_id, tenant_id, source_id, ...)`
stores one row per `appliance_id` and refuses any later call that changes the
bound `tenant_id`, `source_id`, `token` or `public_key_pem` with
`enrollment_binding_conflict`; re-running the same enrollment is idempotent, so
restarts and re-provisioning are safe. Widening an appliance's tenancy is a
delete-and-re-enroll with a new audit trail, never an in-place edit.

This is what discharges the ADR BEAAA-20236 option-A tenancy claim that
"mixed-tenant batches are structurally impossible". The claim is true of P1
*because of this invariant*, not because of anything checked on the wire.

The wire-level control stays as written (BEAAA-20237 F3, ruled **A0** on
BEAAA-24478): a batch carrying any event or determination whose `tenant_id` is
not the enrolled tenant is a deterministic, non-retryable `unauthorized_scope`
rejection taken before any write transaction. Two consequences follow, and both
are deliberate:

- The reject is on the **tenant limb only**. `agent_id` and `policy_number` are
  per-event by construction in the sealed `_make_batch`, so one genuine 0.1.0
  envelope legitimately spans many agents. Gate 1's binding, agent and
  deployment limbs are **producer** obligations, discharged by a future 0.1.1
  that groups at source; a receiver cannot satisfy them by rejecting, because
  the receiver did not choose the grouping. Do not widen the reject to those
  limbs — it would refuse the normal envelope and take ingest to zero
  throughput.
- Receiver-side splitting of a mixed envelope (ADR rule 9 coalescing) is **not**
  approved for P1. The sealed 0.1.0 producer emits one `signature`,
  `batch_hash`, `batch_seq` and `prev_batch_hash` per envelope, so a mixed
  envelope is one signed batch violating rule 3, not a coalesced delivery; a
  receiver that manufactured per-tuple batches would compute its own canonical
  hashes and rule 5's tamper conflict would stop saying anything about the
  producer.

Gate 4 — a rejected envelope mutates no other appliance's ledger — is enforced
by ordering and atomicity: verification, the content boundary, tenant scope and
shape checks all precede the write transaction, and the write itself is a single
`BEGIN IMMEDIATE` transaction rolled back on any failure. Regression coverage:
`test_a_rejected_envelope_mutates_no_other_appliances_ledger` and
`test_enrollment_is_the_p1_single_tenant_invariant` in
`tools/log-tracker/tests/test_appliance_ingest_beaaa20231.py`.

## Content boundary

The adapter name-scans every node of a delivered batch against the sealed
`CONTENT_FIELDS`. This scan is the compensating control for the fact that the
sealed `verify_batch` rebuilds the signed body from nine named keys: an injected
*extra* top-level key is outside both the signature and `batch_hash`, so it
verifies, and only the scan will catch it.

Exactly one node is exempt — `batch["counts"]["by_action_class"]`, the one
contract-v1.0 map whose keys are data values (action classes) rather than field
names, one of which is legitimately `message`. It is addressed **structurally**,
by the tuple of keys walked to reach it plus object identity, and never by a
rendered path string: the sender names its own keys, so a rendered address is
forgeable (BEAAA-20237 F7 / BEAAA-24478 item 1). In place of the name scan that
node is checked by exact equality against the per-class tally of
`batch["events"]`, which the scan has already cleared, so it can hold nothing
the events do not already hold.

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
