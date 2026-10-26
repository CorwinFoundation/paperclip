# Amazon Quick: controlled configuration-copy assessment

Draft technical handoff for AllCloud | BackBond | 25 October 2026

This pilot produces evidence for BackBond's AI agent insurance process and
proposed Quick-side fixes for AllCloud/customer review. It issues no coverage
decision; BackBond Score is N/A.

## Selected route: Path C

Recreate the reviewed agent configuration in a separate sandbox AWS account with
native/IAM-backed registered Quick users. Replace production sources with labelled
synthetic fixtures and create three dedicated identities. The administrator
approves the copy and records configuration differences. Real action connectors
stay disabled; BackBond receives no production login, employee cookie or permanent
AWS key.

The report says **configuration-copy assessment**. Production grants, row rules,
SSO/device controls and real data remain untested.

## Scope and current evidence

The prepared pack has 600 case rows across nine chat families, selected/adapted
from BackBond-authored and pinned licensed sources. HR/finance and healthcare
fixtures are available. Paired controls and multi-turn cases require 790 initial
target calls plus nine readiness calls; failure confirmations are separately
bounded. Calibration and live qualification remain pending.

Quick uses an operator-led setup with three identities, scoped AWS access and
provider logs. Self-serve endpoint checks do not provision that setup.

Transport passed 10/10 benign checks in BackBond's synthetic AWS sandbox. The
expanded runtime still needs release CI and live qualification. No onboarding
time or two-hour SLA is promised.

## Four customer steps

1. **Intake:** identify the customer/admin/security owner, accounts, actual region,
   authentication, sanitized export and fixture variant. Approve synthetic-only
   processing, location/readers, window, budgets and retention. Healthcare uses
   no real patient/staff records.
2. **Sandbox setup:** recreate configuration; install synthetic sources with
   grants and structured row rules; create three licensed chat users; deploy
   restricted access/logging. Verify source versions and log isolation. Shared
   logs need a small Lambda forwarder; direct account-wide delivery requires a
   dedicated synthetic account.
3. **Bounded run:** the dedicated BackBond AWS worker uses temporary access and
   independent conversations. Correlated CloudWatch CHAT_LOGS finalizes answers;
   browser text only paces progress. Unknown submissions halt. External judging
   is off unless the security office approves it; otherwise semantic cases need
   analyst review.
4. **Review and close:** review findings and proposed Quick-side fixes, assign
   owners and scope effort, agree any bounded retest, remove access and close out
   evidence at the agreed deadline.

## Access and data

Access is embedding for three exact users on one HTTPS origin and reading one
pilot log group. Trust names one per-client execution role, ExternalId and UTC
cutoff. Proposed private evidence storage is encrypted BackBond S3 in us-east-1;
named readers and 30-day default retention require customer approval. Version
deletion proof and temporary-worker cleanup are required. Read the separate
handling statement; processing is outside the customer account and may be
outside Israel.

Action/money outcomes, Automate/Desktop, Identity Center embedding, AWS-only
judging, in-tenant execution, recurring runs and Hostinger dispatch are outside
this pilot. Claude Enterprise/MCP is a separate scope. Next: one administrator/
security-owner introduction and an approved export through a private channel.
