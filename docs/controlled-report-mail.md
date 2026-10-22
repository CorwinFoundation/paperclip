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
/var/lib/backbond-scan-intake/controlled-report-mail/mail.sqlite. Alternate --config or --outbox
paths are rejected before opening the outbox or loading credentials. Root must provision the dedicated private parent as described below.
The packaged config is an unarmed installation template.

Before send, the authorized operator must open both acceptance references and
verify the independent Reviewer SHIP comment and QA APPROVED comment ID on
BEAAA-43363 cover this exact new revision and both attachment hashes. Nonempty
reference strings alone are not approval. Record the actual comment IDs in the
service-owned manifest. A failed row is terminal; there is no new-attempt path.
Inbox receipt evidence is write-once.


Runtime custody correction (Root attestation 2026-10-22)

The existing owning unit is backbond-scan-intake.service, running as backbond-qa
UID987/GID980. Root attested PID2950795 and unit SHA-256
`df54c1a2473098901c592e3d6da5b6864d2b969806b93da83fc77c640514c796`.
Revalidate these metadata before any approved installation; drift requires review.
Its existing LoadCredential IDs include scan-mail.json and qa-scanner-tokens.json.
No credential contents, environment dump or process arguments are needed.

/var/lib/backbond is owned by UID992/GID982 mode0751 and is not this service's
state directory. Never borrow that identity, change that directory's ownership or
mode, or widen shared permissions. /var/lib/backbond-scan-intake is already owned
by UID987/GID980 mode0750 and covered by this unit's existing ReadWritePaths.
Root's installation action after scoped QA acceptance is to create only its
controlled-report-mail child, owned UID987/GID980 mode0700, for the fixed
mail.sqlite outbox. Use umask0077 for adapter invocations so DB/WAL/SHM files are
private. Preserve the containing directory's ownership/mode and existing policies.
Do not create an alternate outbox or migrate a prior send reservation implicitly.

/etc/backbond/controlled-report-mail.json remains the fixed config path; it was
absent at attestation. Install the unarmed template root-owned, service-readable
(e.g. root:GID980 mode0640), without granting new credential access. Leave its
run/report/revision null until the new accepted report is available. The template
has no configurable outbox path; both installed CLI paths remain pinned in source.

Invoke only within the EXISTING owning unit context: same UID/GID, sandbox,
ReadWritePaths and existing systemd credential namespace. A future reviewed manual
ExecReload control action may provide that context; none is currently configured.
Do not use a separate unit, a human login with borrowed credentials, new
LoadCredential/read grants, or an environment/credential export. systemd supplies
CREDENTIALS_DIRECTORY to unit processes; retain the existing scan-mail.json
convention and sender BackBond <info@backbond.ai>.

No reload hook is installed or approved by this document. Root must present the
exact control-action wrapper and unit delta for scoped QA review before applying
it. Preserve ExecStart, main PID, stdout handling, all policies and admission holds;
no restart or send. The first reviewed manual control action must run only status
against the unarmed adapter, with umask0077 and output filtered to an allowlist of
safe metadata (e.g. empty slot or state and presence booleans). Suppress raw stdout
receipt JSON and raw exception/stderr text: status can include binding and evidence
references, and may initialize the empty SQLite schema. The wrapper must fail
closed and report only a fixed failure code on error. It must not load credentials,
call send/reconcile, or arm config. Root owns applying that exact reviewed AriClaw
plan after QA; this source change does not perform runtime changes.

QA43363's scoped review covers the changed fixed path, rejection of alternate
paths, private parent/config ownership plan and existing-unit-only context above.
The new report's exact Reviewer SHIP, QA PASS, attachment hashes and subsequent
transport/inbox evidence remain separate gates; source QA is not delivery PASS.
