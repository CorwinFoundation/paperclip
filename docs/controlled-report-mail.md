# Controlled Findings Report internal delivery (BEAAA-43362)

CTO owns this operator adapter. It is separate from Scanner/intake HTTP handlers.
Baseline dd0e1707f53eb3bb2f06b2c6c5310eb4fbf8ebb4 has Resend link/code
mail in backbond/scan-intake/server/app.py, but no reviewed attachment delivery.
This candidate adds no credentials, sender identity, runtime unit, or permissions.

The service-owned config defaults to recipient/allowlist info@backbond.ai. It is
unarmed: run_id/report_id/revision are null. The operator must bind these to the
NEW sole controlled run and its accepted revision after the normal Writer/Reviewer
chain. Do not bind the historical private report. Independent QA must accept this
candidate before runtime installation/use. Admission and budget gates remain
separate and blocked; general intake remains closed and automation paused.

The trusted, service-owned acceptance JSON must contain run_id, report_id,
revision, reviewer_decision="SHIP", qa_decision="PASS", reviewer_acceptance_ref,
qa_acceptance_ref, and pdf/html objects each with path and sha256. Relative artifact
paths resolve from the manifest directory. The reviewer/QA references must name
the existing chain's actual decisions covering these exact hashes and revision.
This is an operator trust boundary: the CLI cannot authenticate human approvals.
Never accept these fields, config, file paths, or DB path from public request data.

The adapter reads each attachment once, hashes those bytes, and submits those same
bytes. It sends both attachments, never a substitute BAC card/link. The persistent
SQLite outbox has one slot for this controlled delivery; a changed run/revision is
rejected. Use one service-owned durable path for all attempts; never replace,
delete, rotate or reset it to retry. Retain DB/WAL files in service backups.

Invocation after release and report acceptance, within existing service identity:

```sh
python3 scripts/controlled_report_mail.py send --acceptance /ACCEPTED_REPORT/acceptance.json
python3 scripts/controlled_report_mail.py status
```

Credential discovery uses the existing SCAN_MAIL_CREDENTIALS_PATH or
CREDENTIALS_DIRECTORY/scan-mail.json convention. No credential file was inspected
while developing/testing this candidate. The provider must be Resend; sender is
the existing BackBond <info@backbond.ai>. Redirects and automatic transport retries
are disabled. A deterministic provider idempotency key is supplemental; the local
outbox prevents replay without relying on provider retention periods.

A committed pending reservation precedes the network call. pending after a crash
means outcome unknown; neither pending, unknown, failed nor accepted can resend.
Definitive rejection is failed; timeout, malformed success, rate limit or server
failure is unknown. Reconcile pending/unknown using actual provider evidence:

```sh
python3 scripts/controlled_report_mail.py reconcile --state provider_accepted --provider-id MESSAGE_ID --evidence PROVIDER_LOOKUP_REF
python3 scripts/controlled_report_mail.py reconcile --state inbox_received --evidence MAILBOX_RECEIPT_REF
```

Confirmed nonacceptance can reconcile to failed, with evidence; there is no reset
or retry action. Any future retry would need a separately reviewed recovery change.
Provider acceptance does not establish inbox receipt. Record mailbox receipt only
from independent actual receipt evidence. Do not treat a queued BAC attachment as
mail delivery. Preserve receipts during rollback; removing this adapter does not
authorize sending by another route.

Validation: focused offline unittest suite covers exact bytes, recipient/config
and acceptance guards, unarmed default, rebind, concurrency, restart/replay,
rejections, unknown/timeouts, crash/pending, and separate provider/inbox receipts.
No mail, scan, deployment, runtime config change or historical-report send occurred.
QA43363 owns independent candidate checks and actual transport/receipt evidence
for the new controlled report after the report passes the existing review chain.

Installed CLI paths are fixed: /etc/backbond/controlled-report-mail.json and
/var/lib/backbond/controlled-report-mail.sqlite. Alternate --config or --outbox
paths are rejected before opening the outbox or loading credentials. The runtime
owner must provision these paths with write access restricted to the service owner.
The packaged config is an unarmed installation template.

Before send, the authorized operator must open both acceptance references and
verify the independent Reviewer SHIP comment and QA APPROVED comment ID on
BEAAA-43363 cover this exact new revision and both attachment hashes. Nonempty
reference strings alone are not approval. Record the actual comment IDs in the
service-owned manifest. A failed row is terminal; there is no new-attempt path.
Inbox receipt evidence is write-once.
