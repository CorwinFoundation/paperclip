# Controlled Starter state candidate — BEAAA-43362 / BEAAA-43364

Source-only, disabled candidate. This is not deployment, provisioning, arming or
dispatch authority. General intake holds and the paused schedule remain in force.
Independent BEAAA-43363 exact-head review and Root's exact target, actor, executor,
expiry and budget confirmation precede runtime decisions.

## Existing deployment coverage

Repository scripts/deploy/scanner-deploy.sh supports image activation with exact
revision/archive/digest/candidate identity; scan-intake extraction from the
authenticated running image; preflight; evidence export; disk maintenance; and
starter-v5 acceptance. It has no permit-state provision/arm command. Its
scan-intake subcommand does not provision Scanner state. Source
infra/live-edge/backbond-scanner-live.service mounts /var/lib/are-scanner read-write.
Root's read-only receipt reports the actual container UID/GID 999, /app and the
existing service name. This source inspection does not establish the installed
wrapper's exact bytes or authorize using it.

The deployment wrapper can install candidate image code under its existing
approved release workflow. Scanner host service-owner / Root must separately
approve and execute private persistent-state installation; are-deploy's limited
sudo wrapper does not currently cover that operation. No new reverse guard
service, credential extraction, firewall window or admin HTTP endpoint is needed.

## State and interface

One SQLite file holds controlled_admission, controlled_paid_calls,
controlled_scan_binding, controlled_tests and controlled_permit_context. Caps
remain fixed in the shared admission primitive: USD10 combined and 100 cases.
No second evaluator allowance is added here. Directory 0700, file 0600,
service UID/GID 999, on the existing persistent mount. Loader uses mode=rw on an
existing database, rejects unsafe owner/modes/symlinks/missing schema, and never
provisions through HTTP. Expiry is checked on load and on every paid reservation.

Local protected JSON record has exactly permit_id (32–128 safe opaque characters),
tenant_id, actor, request_id, target_sha256, token_sha256 and expires_at (Unix UTC
integer). Digests are lowercase SHA256. target_sha256 fingerprints the complete
Scanner HTTP execution body, including credential, endpoint, pack/model/version
and callbacks, after removing only top-level controlled_permit_id. The record
contains digests rather than target credentials. Service owners must derive it
inside the legitimate protected configuration. Do not print/export that input.
No caller-provided price, cap or expiry is accepted.

The Scanner API validates the opaque identifier against authenticated tenant and
the entire execution body. It generates endpoint_config._controlled_permit with
permit_id, tenant_id and target_sha256; caller-supplied descriptors are rejected.
bind_scanner_permit consumes the sole Scanner reservation before kickoff.
The worker loads a fresh connection using load_bound_scanner_permit(descriptor,
scan_id=...), propagates it as runner.controlled_permit and closes it on terminal.
The API-generated descriptor is internal context, not a public substitute for
full-payload validation. A replay/unknown never authorizes a new scan.

## Precise disabled provisioning step for later review

Run only after candidate code is installed by an authorized deployment and the
protected record has been confirmed. Commands are proposed, not executed:

    install -d -o 999 -g 999 -m 0700 /var/lib/are-scanner/controlled-admission
    docker exec -i --user 999:999 backbond-scanner-live-edge       python3 /app/scripts/controlled_permit_state.py provision-disabled       --db /var/lib/are-scanner/controlled-admission/permit.sqlite       < /root/backbond-controlled-permit-record.json

The private record input must already be root-owned 0600. Repeating the exact
record is a no-op; changed bindings fail rather than replacing state. A partial
or failed provision leaves unusable state for local investigation, never
automatically deletes/recreates it. Record the receipt without its protected
contents. Configure ARE_CONTROLLED_PERMIT_DB to that absolute path through the
approved service configuration workflow, separately reviewed. Merely configuring
the loader does not enable admission. The CLI provides a local `arm-once` operation accepting the same exact original
record on stdin. It refuses expired, rebound, consumed, unknown or previously
attempted reservations. Its irreversible arming marker prevents rearming even
after disable without a dispatch. It does not expose an HTTP administration route
or change either general hold. Runtime authority remains with Root; this source
addition does not authorize executing it.

    python3 scripts/controlled_permit_state.py arm-once --db /absolute/private/permit.sqlite < /protected/original-record.json

No reset operation exists. A failed arm leaves the reservation disabled.

## Disable and rollback

Proposed request-preserving disable:

    docker exec --user 999:999 backbond-scanner-live-edge       python3 /app/scripts/controlled_permit_state.py disable       --db /var/lib/are-scanner/controlled-admission/permit.sqlite

This commits enabled=0, leaving consumed identity, paid attempts, counts and
unknown state intact. Confirm general holds remain set before approved code
rollback using the existing exact-release workflow. Keep the database, parent
directory, config record and protected backups; do not remove the volume, replay
the request, clear quota, refund an unknown call, or re-enable consumed state.
Removing loader configuration also fails controlled requests closed, but durable
disable is the first action while the candidate code is available. No timeout
alone reconciles an unknown execution.

## Source integration and no-save boundary

The intake authenticates the selected operator and request before contextual
Pydantic validation for either connection check or scan. Its protected startup
record includes `scanner_permit_id`, the service-owned opaque Scanner identifier.
Intake and Scanner use separate persistent reservations bound to the same complete
execution fingerprint; only Scanner owns the shared preflight/target/judge spend.
Both outgoing HTTP requests carry that same permit identifier. No caller supplies
policy, price, cap, expiry or model-validation override.

`ARE_CONTROLLED_TARGET_CONFIG` names a service-owned 0600 JSON file in a 0700
directory containing exactly `path` (fixed-handler policy path) and
`expected_sha256` (independently confirmed policy digest). It must contain no
payload or credentials. The fixed-handler policy contains only version, kind,
target digest and complete-request price ceiling. Unsupported transports fail
closed rather than dropping execution fields.

The API validates the complete incoming execution body, then passes it through
runtime and worker function arguments in RAM. It is never added to endpoint_config,
permit state, accepted-job records, environment, argv, logs or recovery files.
The target adapter verifies the complete fingerprint, resolved vault secret and
endpoint before construction. Missing memory context after interruption fails
closed; it is never reconstructed or replayed from persistent records.

The authenticated Scanner connectivity route creates this exact adapter from the
memory-only request and digest-only policy. Its normal connectivity handler owns
the sole natural preflight attempt in the same spend ledger, with no scan ID or
case consumption. Intake issues/redeems its ordinary connection proof against the
same normalized fields. General admission holds remain set throughout.

Source tests do not authorize deployment, provisioning, arming or a paid call.
Exact-head independent QA43363 and Root runtime confirmation remain required.

## Consumed terminal failures and slot policy

A scan-binding failure after a successful claim remains consumed, including a
binding write whose response is lost. No Scanner dispatch has occurred. Intake
retains the scan and permit records, marks the scan `launch_failed`, and returns
its newly issued status capability in the response. The caller can poll that
stable terminal status; the token is never recovered from storage. This terminal
unsent state releases the active intake slot under the existing slot accounting;
it does not refund paid allowance or permit retry, reset, or rearming.

QueueFull after a successful claim also remains fail-closed and consumed. Its
unsent quota reservation is cancelled under the existing queue policy; cancelling
a slot never restores the one-use admission. Operators must not retry or rearm.
