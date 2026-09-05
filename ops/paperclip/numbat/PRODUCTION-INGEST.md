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
is the version this adapter is tested against.

## Clean-host install

Every step below was executed end to end on a host prepared by nothing else,
under systemd, for BEAAA-24725 Part C. The transcript is
`artifacts/BEAAA-24725/drill-transcript.txt`. Before that run the three-sentence
summary above was the whole procedure, and steps 3, 4 and 6 were missing, so a
literal install produced a service that crash-looped without ever binding a
port. Re-run the drill against any host image change; a procedure nobody has
executed is a procedure with steps that do not work.

1. Install the sealed artifact. Verify the archive SHA-256 first, then unpack it
   and move the tree to the path the unit passes as `--sealed-root`. The archive
   unpacks to `backbond-appliance/`, not to a version-named directory:

       sha256sum backbond-appliance-0.1.0.zip     # must be dbef6e73...ef8a
       unzip -q backbond-appliance-0.1.0.zip -d /opt/backbond
       mv /opt/backbond/backbond-appliance /opt/backbond/appliance-0.1.0

2. Create the virtualenv the unit's `ExecStart` names, and install the Log
   Tracker package into it:

       python3 -m venv /opt/backbond/venv
       /opt/backbond/venv/bin/pip install ./tools/log-tracker

3. Install the sealed appliance's own dependencies into that same virtualenv.
   The service imports `appliance.batch` at startup to get `verify_batch`, and
   that module imports `cryptography`, which the `log-tracker` distribution does
   not declare because nothing else in Log Tracker needs it:

       /opt/backbond/venv/bin/pip install -r /opt/backbond/appliance-0.1.0/requirements.txt

   Skipping this is not a degraded mode. The receiver exits 1 before it binds,
   `Restart=on-failure` restarts it every five seconds, and `systemctl is-active`
   reports `activating` rather than `failed`, so a health check keyed on `failed`
   never fires.

4. Create the service identity and the two directories the unit needs.
   `ProtectSystem=strict` makes the whole filesystem read-only except the single
   `ReadWritePaths` entry, so the database directory must exist and be owned by
   the service user before first start:

       groupadd -r backbond
       useradd -r -g backbond -s /usr/sbin/nologin -d /var/lib/backbond backbond
       install -d -o backbond -g backbond -m 0750 /var/lib/backbond
       install -d -o root -g backbond -m 0750 /etc/backbond

5. Install the ordered migrations the unit passes as `--migration`:

       install -D -m 0644 ./tools/log-tracker/migrations/0002_appliance_ingest.sql \
         /opt/backbond/migrations/0002_appliance_ingest.sql
       install -D -m 0644 ./tools/log-tracker/migrations/0003_appliance_ingest_agents.sql \
         /opt/backbond/migrations/0003_appliance_ingest_agents.sql

6. Write the two configuration files. The enrollment JSON contains
   `appliance_id`, `tenant_id`, `source_id`, `public_key_pem` and `token_env`.
   The token value itself stays in the environment file, which systemd reads as
   root before dropping privileges, so that file is `0600 root:root`. The
   enrollment JSON is different: the service process opens it *after* dropping
   to `backbond`, so it must be readable by that group.

       chown root:backbond /etc/backbond/appliance-enrollment.json
       chmod 0640          /etc/backbond/appliance-enrollment.json
       chmod 0600          /etc/backbond/appliance-ingest.env

7. Install and enable the unit, then confirm it is serving:

       install -m 0644 ops/paperclip/numbat/backbond-appliance-ingest.service \
         /etc/systemd/system/
       systemctl daemon-reload
       systemctl enable --now backbond-appliance-ingest.service
       systemctl is-active backbond-appliance-ingest.service   # active
       curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8787/healthz  # 200

   `systemctl is-active` returning `activating` means the unit is crash-looping;
   read `journalctl -u backbond-appliance-ingest.service` before going further.

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

## Receipt counts: attested parent, derived child

`appliance_ingest_receipts.billable_actions` is the value the appliance attested
and signed. `appliance_ingest_receipt_agents.billable_actions` is derived by the
adapter from the delivered events, using the producer's own truthy predicate. The
two limbs normally agree, and the child rows are asserted to sum to the parent on
a well-formed batch. They are allowed to disagree: `billable_actions` is not
covered by the histogram equality check, and widening the reject to cover it was
ruled against (BEAAA-24534 item 2), so a batch whose attested total disagrees with
its own events is accepted. A ledger reader reconciling per-agent rows against the
billed total is reading an attested number against a derived one.

## Closed envelope

The accepted top-level key set is pinned to the twelve keys a sealed 0.1.0
envelope carries: the nine `verify_batch` rebuilds the signed body from, plus
`batch_hash`, `signature` and `key_id`. Anything else is a deterministic 422
`unknown_envelope_field`, taken after verification and before the write
transaction. The comparison is against that pinned set, never against a list of
known-bad names, because the field this closes is the one nobody thought to
name: an extra top-level key is outside both the signature and `batch_hash`, so
it verifies, and the content scan sees it only if its own name or a name in its
subtree is in `CONTENT_FIELDS`. A rider called `evidence_tier` is in neither and
was accepted until BEAAA-24725.

The rejection names no field. The sender chose that key, and a sender-chosen
string echoed back is the same content channel a rendered path would be.

A 0.1.1 that adds a thirteenth key trips
`test_a_genuine_sealed_envelope_carries_exactly_the_pinned_keys` red. That is
deliberate: the alternative is the new field being accepted unsigned. Update the
pinned set as part of adopting the new contract, not before.

## Interfaces and observability

Health: `GET /healthz`. Counts only: `GET /metrics`, which requires the same
bearer token and `X-BackBond-Tenant` header as ingest and returns that tenant's
counts alone; an unauthenticated or cross-tenant read is 403. Ingest uses
`POST /ingest` with bearer token plus `X-BackBond-Tenant` and
`X-BackBond-Source`. A 503 is retryable and never advances chain/idempotency
state. A 422 is a deterministic rejection.

`/metrics` carries `batches_accepted`, `events_accepted`,
`determinations_accepted`, `accepted_total`, `duplicates_total`,
`unavailable_total`, `rejected_total` broken down by reason class, and
`chain_tip_batch_seq` per appliance. The counters are process-local and reset
with the unit; journald and the receipt table are the durable record, which is
why this needed no migration. `chain_tip_batch_seq` comes from the receipt table
and does survive a restart, so a quiet appliance and a stalled one stay
distinguishable after one.

Read `rejected_total` before `batches_accepted`. Until BEAAA-24725 a tenant that
had delivered nothing and a tenant whose every delivery was refused returned
identical counters, which made the likeliest pilot failure the one nothing
surfaced.

Every request writes one JSON line to stderr, which journald carries under the
unit:

    {"appliance_id":"a1","batch_seq":2,"event":"appliance_ingest",
     "outcome":"accepted","reason_class":null,"ts":"2026-08-19T21:54:02.488Z"}

`outcome` is one of `accepted`, `duplicate`, `rejected`, `unavailable`. The
default access log stays silenced and this replaces it. Two fields are
deliberately narrower than they could be. `reason_class` is the class only:
`content_boundary_violated:$.a.b` embeds keys the sender named, so a rendered
path in a log would make the log the content channel the boundary exists to
deny. `appliance_id` is written only if it matches `[A-Za-z0-9._:-]{1,64}`, for
the same reason, since it too arrives off the wire. `tenant_id` is never
written; the appliance is bound to one tenant by enrollment, so the appliance
identifies the tenant to anyone entitled to know.

Process failure is `Restart=on-failure` plus journald, unchanged. Note its
limit: a service that fails on every start restarts every five seconds and stays
in `activating`, so it never reaches `failed`. Alerting on `failed` alone would
miss it. Paging, alert routing, scraping and retention are out of P1.

## Rollback

Stop and disable this service. Queue or reject appliance delivery while it is
unavailable; do not route traffic to the test receiver or the superseded
tenant-store implementation.

The append-only receipt database survives and is reopened by the adapter on
restore. That claim was untested until BEAAA-24725 Part C ran it against a
clean-host install; the transcript is `artifacts/BEAAA-24725/drill-transcript.txt`.
Chain state lives in two places, the producer's `chain.state` file and this
receiver's receipt table, so the restore is the step worth rehearsing:

    systemctl disable --now backbond-appliance-ingest.service

While it is down, delivery fails closed and visibly: the client gets a refused
connection, not a slow success, and no receipt row is written. Batches the
producer builds during the window are signed and chained locally and are held,
not lost.

    systemctl enable --now backbond-appliance-ingest.service

On restore, redeliver the last pre-rollback batch first. It answers
`"duplicate": true` and writes nothing, which confirms the receipt table came
back rather than being recreated empty. Then deliver the batches built during
the window in order; the first chains onto the pre-rollback tip and
`chain_tip_batch_seq` advances. A redelivery answering `accepted` with
`duplicate: false`, or a `broken_chain` rejection on the first held batch, means
the database did not survive; stop and escalate rather than re-enrolling, which
would rebase the chain and lose the tamper property.
