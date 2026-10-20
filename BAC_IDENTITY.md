# Starter BAC application identity — proposed, not live-approved

Create one new Paperclip agent principal named `starter-intake-service`, with its
own agent API key, role `general`, no agent-creation permission, and no autonomous
heartbeat. Never reuse BAC Lead 5da6979d, another agent identity, or B3
(BEAAA-42392) credentials/scope. No live access is requested by this PR.

The minimum effective permission is create cases assigned to the configured BAC
Lead, read only those service-created cases for reconciliation, and append
comments only to those cases. A general role alone is not evidence of this
restriction: BAC must enforce it server-side (or through an authenticated,
restricted gateway) before installation. Broad company read/write is unacceptable.

## Allowed route contract

These are the only client routes currently implemented and exercised by the
fixture in `backbond/scan-intake/tests/test_bac_intake.py`:

- `POST /api/companies/{company_id}/issues`: body `{title: "Starter request " +
  request_id, description: JSON({request_id, customer_ref, agent_ref, scope_ref,
  code_ref}), status: "todo", priority: "medium", assigneeAgentId: lead_id}`.
  Require 201 with matching company/Lead and nonempty issue ID. Commit local
  intent first; never repeat POST for that request ID.
- `GET /api/companies/{company_id}/issues?q={urlencoded_request_id}`: no body.
  Authenticated reconciliation only; require 200 and exactly one matching title,
  full reference payload, company and Lead. Empty/ambiguous/foreign results remain
  unconfirmed. No dispatch follows an unconfirmed response.

- `POST /api/issues/{service_case_id}/comments`: body `{body: JSON(event)}`;
  require 201 with nonempty comment ID, matching issueId and exact event body.
- `GET /api/issues/{service_case_id}/comments`: no body; require 200 and
  exactly one comment with matching issueId and exact event body to reconcile an
  uncertain append. Empty, foreign, malformed or duplicate matches stay closed.

All routes use the new principal's Bearer key and disable redirects. Comment
routes are restricted to this principal's service-created cases. The restrictive
fixture now exercises these routes; live ACL approval remains outstanding.

The implemented envelope is `{event_id, request_id, case_id, scan_id, type,
revision_sha256, reference, occurred_at, snapshot?}`. The event ID hashes the exact binding
and content except occurred_at. Lifecycle snapshots retain their first persisted
observation time when unchanged, so retries and restarts keep the same event ID. Types are `lifecycle`, `evidence`, `report_revision`,
and `send_receipt`; only lifecycle and evidence are currently wired. The revision
is a SHA-256 of the local state projection or stored result revision. References
are opaque intake pointers; private result rows never enter BAC comments.

SQLite commits event intent before network I/O and atomically claims its single
append attempt across processes. After any uncertain response, retries only read
comments. They never repeat POST, even after an empty read: ordinary Paperclip
comments provide no atomic idempotency key. A crash before the actual append may
therefore require operator reconciliation; it cannot silently produce duplicates.
The worker uses persisted bounded retry delays and projects one scan per tick.

Missed terminal callbacks use the existing authenticated Scanner
`GET /scan/{scanner_id}` customer contract with the intake partner's tenant token.
Only matching scan IDs with supported terminal status and result rows (or an
explicit zero-row failure) enter the existing callback translator/queue. Missing
auth, rows, unknown/foreign or ambiguous remote state never completes or
redispatches a scan. Partial-completion metadata remains incomplete. Local
terminal state reconstructs evidence events after a restart. Canonical `complete`
is accepted alongside the retained translator compatibility spelling `completed`.
`incomplete` and `partial` require rows; zero-row recovery requires `failed`,
`incomplete=true` and a failure reason, matching the existing webhook contract.
Rows are bounded to 100 and must have unique IDs through the callback translator.

The worker also reads the existing owner-scoped authenticated
`GET /scanner/v1/scans/{scanner_id}/progress` contract. Lifecycle snapshots include
local status/outcome, numeric processed/accepted/usable/unavailable counts,
allowlisted state/phase, ISO heartbeat/activity/update time, categorical reason
and first observation time. A heartbeat older than 120 seconds is marked
`stale_progress`; it does not terminate or redispatch a scan. Unknown progress
stays unavailable. Raw reason text, prompts, auth and arbitrary nested upstream
fields are excluded. Only an authenticated accepted-pack count supplies the
denominator; local terminal fallback never substitutes returned-row count.

## Public lifecycle projection

The existing `POST /scan/v1/scan-status` status-token route now adds
`lifecycle={version:"starter_lifecycle@1", intake_status, report_status,
delivery_status, updated_at}`. Execution fields remain separate. Durable
acknowledgement maps to `recorded`, uncertain intent to `unknown`, an existing
binding awaiting intent to `pending`, and absent binding to `unavailable`.
`updated_at` is the persisted first observation of that intake state in ISO UTC,
or null when unavailable. It is not the poll time on every request.

The agreed full intake enum is pending/recorded/failed/unknown/unavailable;
report is awaiting_results/drafting/review/changes_requested/awaiting_ceo_approval/
approved/unavailable; delivery is not_ready/pending/sending/provider_accepted/
failed/unknown/unavailable. This component emits report and delivery `unavailable`
until BEAAA-42596 supplies durable authority. It never infers approval from scan
completion or provider acceptance from queued mail. Unknown fields or versions
must display unconfirmed. No internal identities or references enter lifecycle.

A TestClient response from this route is passed to the unmodified BEAAA-42620
page consumer at dcea210feeac38ce4e352e2364be4b6ed921b54b in the fixture suite.
This proves recorded intake and unavailable downstream states across the boundary;
it does not prove report/delivery transitions that have not been implemented.

## Evidence resolver integration remains open

`intake:<scan_id>` is a correlation pointer, not a released BAC tool. The private
`BACIntake.resolve_evidence` primitive validates the exact durable request/case/
scan/revision binding and returns verdicts by ordinal without prompts, replies,
raw reasons or target details. Tests deny every mismatched binding. It exposes
no public route and grants no caller authority.

BEAAA-42618's gateway owner must expose an authenticated, case-scoped resolver
through the permitted Analyst/Lead tool surface, authorize the actor's case
before invoking this primitive, and prove foreign-case denial. QA BEAAA-42593
must exercise the released tool as Analyst/Lead. Richer private evidence review
requires its own approved scoped contract. Until that integration is released,
the evidence handoff remains blocked; SQL access and copied credentials are not
accepted workarounds.

Never call agent/key management, hires, approvals/approve, issue checkout,
issue PATCH/DELETE, other companies/cases, or direct email-provider routes from
this identity. It cannot approve its own report or send a customer report.
CEO exact-revision approval and TO info@backbond.ai provider receipt remain
separate mandatory release gates. Provider acceptance does not prove inbox receipt.

## Secret and reference storage

On AriClaw install the new key in a root-owned service environment file
(`/etc/backbond-qa/bac.env`, mode 0600) consumed by the intake systemd unit as
`QA_BAC_TOKEN`. Do not put it in source, command arguments, case text, debug HTTP
logs, or exceptions. Disable request/Authorization logging. Company and Lead IDs
are configured separately. Existing SSH keys stay on AriClaw and are never copied.
Case references point back to the private intake scan record; no endpoint,
customer email, auth, raw partner code, prompt, or credential hash goes to BAC.

## Founder-reviewed live handoff

1. Ari reviews this spec and the effective server/gateway ACL proof; CEO arranges
   BAC Lead ownership. Live acceptance remains an explicit open gap until approval.
2. Install the new principal/key and restricted ACL, then set QA_BAC_URL,
   QA_BAC_COMPANY_ID and QA_BAC_LEAD_ID. Keep launch closed during configuration.
3. Run one BackBond-owned fixture request through intake. Inspect local committed
   intent, exactly one BAC case/Lead assignment, acknowledgment, then one Scanner
   dispatch. Drop the acceptance response and reconcile the same request ID;
   require the same case and no second POST. Reconciliation now uses an
   authenticated lifespan worker call to `BACIntake.reconcile_pending`, one intent
   per tick with persisted backoff. Original-reservation dispatch recovery is implemented with ephemeral payloads;
   expiry and restart retain interrupted local state for lifecycle projection.
4. Before full acceptance, require correlated lifecycle/evidence receipts, exact
   report revision handoff, BAC CEO approval of that digest and durable provider
   receipt addressed TO info@backbond.ai. Lifecycle/evidence outbox is implemented; CEO revision approval and provider
   receipt remain the separate BEAAA-42596 implementation scope.
5. Roll back by closing launch, stopping intake, revoking this new key/ACL, and
   restoring the prior reviewed source/config. Preserve SQLite intent/case IDs
   and receipts. Missing BAC configuration must remain fail-closed; never bypass
   the BAC gate or replay ambiguous dispatches during rollback.

### Starter final report authority (BEAAA-42596)

Operations owns the Starter final revision, independent review, Lead decision and delivery to
info@backbond.ai. Intake only hands off durable results and polls the acknowledged Operations
status. Its former local author/review/decision HTTP routes return 410 even when old
QA_RELEASE role credentials are configured. The worker does not consume local report decisions.
A missing Operations acknowledgment remains awaiting_results/not_ready; a local approved revision
cannot open a BAC-bound customer report token. Authenticated case evidence reads and already
mailed pre-BAC legacy report access remain supported. This source contract does not activate
workers, authorize a live scan/send, or prove inbox receipt.
