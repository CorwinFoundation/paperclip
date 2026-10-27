# Amazon Quick configuration-copy pilot: data handling

BackBond draft for customer review | 25 October 2026 | Path C

**Scope.** This pilot assesses a sandbox copy of the approved Quick agent
configuration over synthetic sources and three dedicated identities. It supports
BackBond's AI agent insurance evidence process; it issues no coverage decision.
BackBond Score is N/A. Production permissions, SSO/device controls, real connected
data and business actions are outside this assessment.

**Data.** We store the reviewed, sanitized configuration; synthetic source/setup
references; test prompts and replies; provider account/user/agent/message and
conversation identifiers or hashes; call budgets and evidence receipts; findings
and remediation notes. Expected answers and scanner code are private operator
material and are not installed in the target.

**Healthcare boundary.** No real patient, staff or hospital records are authorized.
Every fixture record/document is labelled synthetic and identifiers use SYN-
prefixes. If unexpected real information appears, testing stops. Restrict access
and follow the agreed customer incident procedure for prompt deletion of all
copies/versions and notification, without analysing that information as a test
result. The customer security/privacy owner must approve this procedure before
launch.

**Location and readers.** Proposed execution and private evidence storage are in
BackBond's AWS account in us-east-1, including S3 server-side AES256 encryption and
a distinct customer prefix. This is processing outside the customer account and
may be outside Israel. Before launch, record the customer's approval, exact
bucket/prefix, named BackBond operators and AWS reader roles. Ari Katz is the
proposed accountable operator; additional readers must be named. The per-client
worker role is restricted to its prefix. Authorized AWS account administrators
remain privileged; access review is a release condition, not an assertion that
only one person can technically read the account.

Any local review/export copies require an explicitly approved location, named
readers and the same deletion plan. BackBond's own synthetic qualification uses
local operator review copies. Running the worker in AWS does not establish
AWS-only handling; a customer requiring that restriction needs a separately
qualified review/export process before launch.

**External processing.** The semantic judge is off by default. Enabling OpenRouter
requires written customer/security approval in both processing and operator
scope, an approved credential and a persistent budget. Relevant synthetic prompts,
replies and source context then leave AWS for that evaluator. Otherwise semantic
cases remain inconclusive for analyst review. AWS-only automated judging is not
implemented.

**Retention and deletion.** Default retention is 30 days after run completion,
subject to the signed scope. Record a deletion owner and UTC deadline before
launch. Delete raw evidence, configuration copies, temporary worker data and S3
object versions under the agreed scope; verify with an unfiltered version listing
and a deletion receipt. An S3 delete marker alone is not deletion proof. Role
expiry and stopping EC2 do not delete evidence. Any retained minimal audit record
requires a separately stated purpose and retention period in the agreement.

**Approval before launch.** Customer: [name]. Administrator: [name]. Security/privacy
owner: [name]. Approved location/readers/retention/incident procedure: [reference].
Evaluator: off unless separately approved. This is a draft; no customer approval
or independent audit evidence accompanies it yet.
