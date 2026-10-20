# Starter BAC gateway candidate

This source candidate implements BAC_IDENTITY.md's intake routes and a narrow native decision read. It is
not activated. No credential is created or installed by this change.

`bac_gateway.py` pins BAC company 60bcddf3-87be-4197-8f1c-846bd967db11 and
Lead 5da6979d-4906-49d7-a87b-4302d2b7dce2. Intake authenticates with a distinct
client token. Only the gateway receives the upstream agent token. Its principal
must be dedicated to this gateway and never shared with another writer.

The HTTP boundary rejects unsupported methods, paths, query parameters, duplicate
JSON keys, extra body fields, changed assignment and malformed opaque references.
It does not expose arbitrary issue reads, keys, agents, approvals, checkout,
PATCH or DELETE. Opaque references must be private intake record pointers;
Customer, agent and code pointers must equal `intake:<request_id>` and scope
must name a supported Starter pack. Syntactic validation cannot establish what a caller's arbitrary string means.
No private endpoints, prompts, email, credentials or raw partner codes belong
in these fields. Responses project the allowed fields rather than returning
upstream issue objects. Comment responses include only exact locally committed
events authored by the gateway principal, with no human author.

SQLite commits the request/event payload before the only possible POST attempt.
The primary key and immediate transaction serialize independent workers. A retry
with a different payload fails. After timeout, redirect, malformed acknowledgment
or process restart, recovery uses authenticated GET only. An empty read never
permits another POST. A crash after the claim but before the POST can permanently
leave an unconfirmed operation; operator investigation is required. This favors
avoiding duplicate side effects over automatic retry. Preserve the database.
Recovery requires exactly one matching record with exact references, company,
Lead and `createdByAgentId` equal to the gateway's dedicated upstream agent.
Comment access rechecks current ownership. A changed Lead or ambiguous response
closes access. These checks require BAC to return truthful server-assigned author
fields; the CTO read-only deployed-source verification is recorded below; live credential acceptance remains pending.

## Native Lead decision read (source contract)

`GET /api/issues/<owned-case-id>/starter-report-approval?scan_id=<scan>&revision_sha256=<sha256>`
returns zero or one exact decision. The gateway revalidates case ownership,
requires a locally persisted report_revision event with the same scan/digest
and its native upstream acknowledgment (a local POST intent alone is insufficient),
and reads comments through its authenticated native upstream connection.
A native comment must have the pinned company, case, Lead authorAgentId,
null authorUserId and no deletion timestamp. Its JSON body has exactly
`type: report_approval`, `request_id`, `case_id`, `scan_id`,
`revision_sha256`, and `decision: approve|reject`. Every binding must match.
Only those fields plus native comment ID and the verified Lead ID are returned.
Multiple matching decisions fail closed; this operation never selects a winner.
The ordinary comment route remains service-event-only. Intake cannot write an
approval through either route, and body author claims grant no authority.

The native author-field contract was verified read-only on deployed Paperclip
3306fa03451e45f5c55458dc838ffd659947b23e by CTO. This JSON decision envelope
is the gateway source contract for report integration; the report owner must
consume the exact digest and decision before release. The read alone performs
no report approval mutation or delivery. Live identity acceptance and report
consumer integration remain pending on the existing delivery work.

## Configuration draft and live setup

After the bac_identity decision on BEAAA-42590, an authorized operator should:

1. Supply a dedicated BAC gateway principal with no autonomous heartbeat or
   agent-creation capability. Verify BAC's server-assigned author fields with a
   BackBond-owned fixture. Keep the upstream token out of intake.
2. Install source under `/opt/backbond/scan-intake/server`. Use the existing
   scan virtualenv with FastAPI, httpx and uvicorn. Create system user
   `backbond-bac-gateway` and use the draft unit in `systemd/`.
3. Set `/etc/backbond/bac-gateway.env` root:root 0600 with
   `BAC_UPSTREAM_URL=http://127.0.0.1:3100` on BAC VM2066639 and
   `BAC_UPSTREAM_AGENT_ID=<dedicated-gateway-agent-id>`. Store distinct tokens
   at `/etc/backbond/bac-gateway/upstream-token` and `client-token`, root:root
   0600, directory root:root 0700. systemd LoadCredential exposes read-only
   copies to the gateway service. The state directory is service-owned 0700;
   SQLite files are 0600 through UMask 0077.
4. Put only the client token in intake's root-owned 0600 environment file as
   `QA_BAC_TOKEN`; set `QA_BAC_URL` to the existing BAC HTTPS origin plus `/starter-bac-gateway` and
   company/Lead to the pinned values. Never log Authorization, request bodies,
   upstream response bodies or exception text. The factory fails on missing
   credentials or an invalid upstream origin. Both bearer tokens must differ.
5. Review the nginx insertion draft against the existing approved host before activation;
   no ingress or firewall change is authorized here. Bind uvicorn to loopback,
   disable access logs and enforce the 8KB body limit at the proxy. No service
   activation, DNS mutation or credential minting is part of this source task.
6. On live authorization, exercise one fixture, lost acceptance, restart and
   ownership denial. Require one upstream POST and the same recovered case ID.
   Keep intake launch closed until all live identity acceptance checks pass.

Rollback: close intake launch, stop the gateway, revoke its dedicated upstream
and client tokens, restore the prior reviewed source/config, and preserve the
SQLite operations database. Do not reopen launch by bypassing BAC. Restoring an
older database can lose POST claims and permit duplicates; do not replay or
replace the operations database during source rollback.

## Local verification

Run `python3 -m pytest -q backbond/scan-intake/tests/test_bac_gateway.py
backbond/scan-intake/tests/test_bac_intake.py` from the repository root. Tests use
the real FastAPI HTTP surface and a deterministic upstream transport. They make
no live BAC writes. Live deployment and credential acceptance remain unverified.

## Scoped evidence tool source contract

`POST /tools/starter/evidence` accepts exactly `request_id`, `case_id`, `scan_id`,
and `revision_sha256` (lowercase SHA-256). No query parameters are allowed.
A distinct bearer credential identifies an Analyst or Lead through a protected
server-side mapping. A second protected mapping grants that actor explicit case
IDs. Caller-supplied role/actor fields and intake credentials grant no access.
The private `BACIntake.resolve_evidence(store, **binding)` must then verify the
acknowledged durable request/case/scan binding and exact stored result digest.
The response contains only those four binding fields, outcome
(`complete`, `failed`, `incomplete`) and results with ordinal and verdict
(`pass`, `fail`, `could_not_run`). Other fields are discarded. Denials return a
generic 403 without private resolver errors. Unknown cases are denied before
calling the resolver. No SQL or credentials belong in BAC comments.

Composition is explicit: `create_app(gateway, EvidenceTool(intake=bac_intake,
store=scan_store, actors=protected_actor_mapping, grants=protected_case_mapping))`.
Mappings are operator-owned, never derived from event JSON. They are snapshotted
at composition; revocation requires rebuilding/restarting that composition.
The existing standalone `app_from_env` intentionally leaves the tool disabled:
it has no access to the private intake/store. CTO integration must compose this
boundary with the private resolver from PR #1193 and provide protected per-actor
credentials and case grants before runtime use. No new live access is enabled.
QA should prove foreign actor/case/request/scan/revision denial, response
projection and missing-composition denial. Current HTTP tests use a resolver
fixture; integrated durable-store verification remains required after composition.


## Existing BAC host deployment candidate (draft)

CTO verified the existing nginx host `https://srv2066639.hstgr.cloud` and
AriClaw direct socket source `46.101.105.87`. The site is
`/etc/nginx/sites-enabled/paperclip-customer-operations` on VM2066639.
Insert `systemd/bac-gateway.nginx-location.example` inside its existing TLS
server; keep its existing UI/auth location, Scanner callback, certificate and
HTTP redirect. No Caddy deployment is required on this host.
Intake uses `QA_BAC_URL=https://srv2066639.hstgr.cloud/starter-bac-gateway`.
The trailing slash on nginx proxy_pass strips the prefix; the gateway retains
all native-route/body/ownership validation. The source restriction uses the
socket peer, never a caller-supplied forwarding header. The upstream token
stays in the gateway service on BAC; intake receives only the client token.

The pending bac_identity decision must authorize activation and this insertion.
After authorization, validate the complete existing-site configuration with
nginx -t before reload; verify AriClaw authenticated access, foreign-source
403, unchanged UI and Scanner callback behavior, 8192-byte body limit and one
owned fixture. The CTO tested a temporary complete nginx draft successfully;
this follow-up has not revalidated a live configuration or activated services.
Rollback removes only the inserted location and stops the gateway, preserving
the original site and durable operations database. The Caddy example is for
alternate environments only and is not the verified host deployment path.

## Private intake-host composition

`create_evidence_app(intake=bac, store=store, actors=protected_actor_mapping,
grants=protected_case_mapping)` composes the actual PR #1193 SQLite resolver
without a gateway principal. Run this boundary on the private intake host;
the BAC-VM gateway must not receive a copy of the private database, and intake
must not receive the gateway upstream credential. The factory exposes only the
evidence operation, rejects reuse of the intake bearer as an actor credential,
and provides no BAC API passthrough. It is an explicit factory, not an enabled
public intake route. Protected actor credentials, case grants and authenticated
BAC-to-intake routing remain operator configuration before activation.

The integrated test reopens the actual intake and scan SQLite stores before
Analyst/Lead HTTP reads. It proves foreign actor, intake credential, foreign
case/request/scan/digest, query and native API denial, fixed response projection,
and invalidation when the durable result revision changes. It uses no live
credentials or network. Run both focused suites; integrated candidate result:
97 passed. Hostname/source-IP and native author-field verification are recorded in the
CTO verification handoff. Live credential acceptance remains pending.

## BEAAA-42728: unknown native outcome recovery

The original gateway proof retained a gateway 403, not a native POST response.
Native acceptance is unknown. Comment `3b0bdae9-165e-43f0-89de-aadd018a9d6b`
is a system/recovery comment, not evidence of this event. No native attribution
change is justified by that observation.

The additive `event_attempts` table records a random attempt ID and start time
before event POST I/O, native HTTP status and an opaque response ID when available,
and a separately authenticated confirmed comment ID. It stores no native response
body, headers, exception text or credential. This is event-specific diagnostic
metadata, not a claim that the native API provides idempotency. Existing operation
rows remain untouched; no attempt/status/identity is invented for historical rows.

Outcomes:

- `legacy_unknown`: retained intent without attempt evidence; remains quarantined.
- `uncertain`: transport failure, interrupted attempt, malformed/unauthenticated
  201, redirect, conflict, rate limit, or server error; remains quarantined.
- `refused`: explicit native 400/401/403/404/405/413/415/422 response. Terminal
  refusal for this intent; never automatically remove the claim or retry POST.
- `confirmed`: exactly one comment with the exact durable event body, case ID,
  gateway author agent and null user author. Response ID alone is insufficient.

An authenticated client resubmitting the identical intent only performs native
GET reconciliation after the durable claim exists. No match, multiple matches,
Board/system/Lead authors, and wrong event/case bodies cannot establish success.
A failed reconciliation never authorizes a new native POST. The HTTP failure
remains 403 for client compatibility, with a safe machine-readable `outcome` for
these event results. GET/ownership failures still fail closed generically.

### Platform-only installation and recovery handoff

No installation or live writes were performed by this source task. Platform owns
activation on BEAAA-42618. Use this successor's `server/bac_gateway.py` as the only
runtime replacement; existing credentials, service unit and ingress stay intact.
Resolve the *actual* active WorkingDirectory/ExecStart from
`systemctl cat backbond-bac-gateway` before replacement; the draft default is
`/opt/backbond/scan-intake/server`, with state at
`/var/lib/backbond-bac-gateway/gateway.sqlite`.

1. Stage the successor file, verify its SHA-256 against the source commit, stop
   the gateway, and preserve the old runtime file and a consistent SQLite backup
   using SQLite's backup API. Keep backups private with the existing owner/modes.
2. Atomically replace the active `bac_gateway.py`, preserving its owner/mode.
   Restart the existing unit and verify health and route denial boundaries.
   Startup creates the new metadata table without rewriting historical operations.
3. Preserve event
   `cc76f710d3ed8fb6a0127430e0b6a012be8745af12edf128fbbe05fee7d7cb73`
   and its existing payload/case binding. Under Platform's authorized verification
   window, reconcile only that exact durable payload via the existing gateway
   route (which will issue native GET, never another native POST for that claim).
   If no unique authenticated match exists, record `legacy_unknown` and keep it
   quarantined. Do not call it succeeded, edit authors, delete the claim, change
   the event timestamp/body, or create a replacement event to bypass quarantine.
4. Complete positive lifecycle, exact-revision Lead/report boundary and launch
   verification separately. This correction proves recovery safety in tests;
   it does not establish that the historical event or live lifecycle succeeded.
5. Rollback: stop the unit and restore the prior runtime file, then restart.
   Retain the database including all operation claims and additive attempt records;
   do not restore stale pre-attempt state that could permit another POST.

Validation: `python3 -m pytest backbond/scan-intake/tests/test_bac_gateway.py
backbond/scan-intake/tests/test_bac_intake.py -q` (107 passed). No paid CI used.


## Recovery composition with native Lead decisions

The recovery successor retains the approved native Lead-decision read contract.
A report intent alone is insufficient: the exact native service event must be
acknowledged, and the decision must have the pinned Lead author, company, owned
case, request, scan and revision. Recovery metadata does not grant approval.
The combined focused suite passes 126 tests, including uncertain report
acceptance followed by authenticated Lead read and GET-only reconciliation
across restart with exactly one native POST. Source tests are not live acceptance.

### BAC-63 ownership diagnosis (20 October 2026)

Authenticated native GETs of the proof issue, activity, and recovery-actions
show that `recovery.reconcile_stranded_assigned_issue` moved ownership from Lead
to the gateway creator at 11:33:15.466Z, after Lead run
`0e1c2a78-de89-465f-a5ca-1b3c66bbdc43` failed with `adapter_failed`.
Audit `5feb508f-5877-4601-9c0e-24a004b13de4` identifies recovery action
`779f4bfd-4f28-4f30-a3ad-2ce432559003`; its previousOwnerAgentId and
returnOwnerAgentId both equal the required Lead. The current case is blocked,
assigned to the gateway, with null checkout/execution run IDs. This is native
recovery reassignment, not a gateway assignment request or author-persistence bug.
Installed native release 3306fa03451e45f5c55458dc838ffd659947b23e
services/recovery/service.ts:2589 explicitly assigns recoveryAction.ownerAgentId.

Platform repair path (native maintenance only, outside the intake gateway):

1. Freshly read the exact issue and active recovery action; abort if company,
   owner, return owner, action ID, blocked state, or absence of active execution
   differs. Preserve the gateway database and historical event payload unchanged.
2. Inspect/fix the recorded Lead adapter failure using existing operational
   authority before returning execution to Lead. Reassignment by itself does not
   fix that failure or establish a durable operational owner.
3. With an existing native operator identity authorized for issue mutation and
   tasks:assign, PATCH only `/api/issues/04a59c0e-1631-4f17-811a-ce6bfafc590a`
   with `{"assigneeAgentId":"5da6979d-4906-49d7-a87b-4302d2b7dce2"}`.
   Preserve status and other fields. Native assignment routes enforce tasks:assign;
   being recovery owner alone does not prove assignment permission. Do not use
   Lead credentials, create keys, broaden the gateway, or bypass a native denial.
   If no existing authorized operator path is available, Platform must record that
   precise blocker; this handoff does not authorize credential provisioning.
4. Re-read the case and native assignment audit. A still-active recovery action
   must remain visible and receive its own valid resolution after operational
   repair; resolving it alone does not restore assignment. Do not mark the case
   done merely to clear recovery. Stop if recovery reassigns it again.
5. Reconcile the original event through authenticated native GETs only. Require
   one exact durable body on this exact owned case, service author and null user.
   Missing/duplicate/system/Board/Lead matches leave acceptance UNKNOWN. Existing
   gateway resubmission of the identical durable intent invokes only native GETs;
   never call native comment POST, delete the claim, or reconstruct an event.

Six deterministic ownership-return regression cases verify that gateway-owned
cases fail closed and that returning to Lead permits confirmation only for a
unique exact service-authored historical event. Missing, duplicate, system,
Board and Lead comments remain unconfirmed. All six preserve the durable claim
byte-for-byte and issue no upstream POST after initial case creation. These tests
simulate operator reassignment; they do not certify live native operator rights
or a live repair. No runtime replacement is required for this diagnosis.
