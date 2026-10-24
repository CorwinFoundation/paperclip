# Automatic Findings reports

Completed portal scans use the existing Findings Report Writer and independent
Findings Report Reviewer. The CEO coordinates their native tasks. A durable
gateway workflow binds the intake request, complete saved exchanges, evidence
archive, role identities, two review rounds and exact PDF/HTML hashes. Only a
closed independent SHIP review with passing mechanical checks releases files.
Routine FIX requests route automatically to the same Writer. Incomplete evidence
or unresolved review failures remain held; there is no legacy report fallback.

Handoffs use native `[@Name](agent://agent-id)` mentions, committed in the same
PATCH as the Writer's blocked status or Reviewer's done status. The CEO immediately
assigns independent review, or returns the same Writer task with precise fixes and
a native wake mention. Ordinary `/agents/` links do not wake agents. The existing
two-round limit and separate Writer/Reviewer identities remain enforced.

The gateway listens to the native company's authenticated WebSocket event stream
using its existing protected credential. Issue events only wake reconciliation;
all author, closure, evidence and artifact checks reread durable native records.
Intake waits on the gateway's authenticated `/findings/v1/workflows/changes`
cursor endpoint and wakes its existing delivery outbox after committed changes.
Notifications cannot approve files, change recipients or clear delivery leases.
Reconnects reconcile durable state, and periodic checks remain recovery only.
Install `server/requirements-findings-events.txt` in the gateway virtual environment
before deployment. The WebSocket is local to the native host; no new public route,
native permission or credential is required. The changes route stays inside the
existing intake-host-only gateway prefix and uses the existing intake token.

The existing owner-authenticated Scanner result route supplies `selection`
from its saved accepted request and pre-dispatch form. It exports expected
family counts, selected case IDs and integrity hashes; credentials, scenario
payloads and the private selection seed stay on Scanner. No SSH identity,
new privileged service or broader API grant is needed. Intake checks tenant
ownership and binds the record to the saved results and corpus pins before
packaging it for the agents. Missing capture evidence is recorded and retried.

For a workflow created before selection capture, an append-only supplement
preserves the original request, archive and verdict. A service-created native
evidence-recovery child wakes the CEO. Writer and Reviewer must attest the new
archive revision independently; old approvals cannot release it. Supplement
retries are idempotent and do not create another production Writer.

Founder default restored 24 October 2026: after independent approval, Intake
publishes the exact reviewed PDF/HTML behind recipient authentication and
automatically emails the website link to **info@backbond.ai** through the
existing mail service. The website sends a six-digit code to that mailbox;
the link alone cannot read either artifact. Sessions expire, are report-scoped
and can be signed out. The reviewed HTML is sandboxed; PDF/HTML bytes remain
unchanged. No agent send, additional Founder decision or customer email is
required. New workflow requests bind the info notification destination.
The website report policy permits frames only from the existing
`https://api.backbond.ai` origin. Artifact responses sandbox the reviewed HTML
and disable scripts, forms, external assets and caching.

The publication token and notification payload are durable and immutable.
Provider idempotency covers retries and restarts; an uncertain attempt beyond
the provider window is held for reconciliation. Older attachment attempts
already in progress keep their original key, bytes and recipient. Previously
sent reports are not automatically replayed. An explicitly selected existing
approved report may be published for operator acceptance testing without
another scan or changing its review or original delivery receipts. Provider
acceptance is reported as acceptance, not proof of inbox arrival or opening.

Enable `BAC_FINDINGS_ENABLED=1` on the restricted gateway before enabling
`QA_FINDINGS_REPORTS=1` at intake. Set `QA_FINDINGS_REPORTS_SINCE` to the cutover
timestamp. Existing gateway and mail credentials remain protected on their
current servers; no native agent key is exposed to intake. Historical customers
are excluded. `QA_FINDINGS_REPLAY_SCAN_IDS` permits an explicitly chosen retained
internal scan for acceptance testing, without making a new Scanner request.
Remove that replay setting after verification. Intake and gateway databases need
private backups before deployment. Roll back code and mode together; never
silently downgrade an in-flight Findings report to the retired chain.
