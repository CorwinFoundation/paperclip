# BEAAA-45026: sender and recipient Inbox activation

Status: implementation checkpoint, 2026-10-31. Owner: CTO.

Sender for findings reports and verification codes: `BackBond <reports@backbond.ai>`.
Recipient remains `info@backbond.ai`. Provider acceptance does not verify placement.

Existing AriClaw activation remains blocked on Ari running the previously supplied
idempotent operator command for candidate 622d675. Agent sudo access was denied;
this sender change does not resolve or replace that host action.

Access/action owners:

- Ari, provider/domain administrator: expose read-only Resend domain verification
  metadata and DNS status to CTO through the sealed runtime access path. Confirm
  backbond.ai is verified for sending; retain sanitized SPF and DKIM results and
  alignment with the From domain. Do not post API keys or raw email headers.
- Ari, info@backbond.ai mailbox owner: provide scoped read-only mailbox access
  through the sealed runtime path, or perform the first correlated folder lookup.
  Record provider message ID, scan ID, recipient observation time, actual labels
  and explicit INBOX presence/absence. Info label and mailbox-wide matches fail.
- CTO: connect a recipient mailbox observer to the monitor, retaining observations
  with message/scan correlation; demonstrate one actual Inbox arrival, provider
  acceptance and delivery separately, and opening its results link against the
  matching persisted results. Demonstrate an injected placement failure and real
  incident/push receipts. Local fake adapters do not constitute these receipts.

Reported incident cause: Ari reported the Syllable message delivered at 18:21Z
but the Gmail Info filter archived it outside Inbox, also hiding it from Outlook
Inbox views. This is reported history, not new verification. A sender change
must not be assumed to bypass that filter. Mailbox owner must inspect its actual
rules during activation; any adjustment must preserve the named recipient.

The monitor emits a separate deduplicated critical placement incident after the
four-hour completion deadline for accepted messages lacking verified Inbox
observation. Current worker integration has no mailbox observer and therefore
fails closed as unverified. Observer wiring, provider authentication checks,
link checking, deployment and live alert/push demonstrations remain unfinished.
