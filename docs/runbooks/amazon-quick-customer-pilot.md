# Amazon Quick customer pilot operations

Internal operating runbook | 25 October 2026 | Depends on connector #1269 and internal profile #1272

This packages `internal-agent-knowledge@1` for an AWS worker: capture, the existing
grader, durable evaluator budget and a readable report. It is a scoped report-only
assessment with BackBond Score N/A. It does not deploy the public Scanner API or
connect Hostinger's job queue. A build or scripted fixture is not customer evidence.

## Browser startup pacing

The batch runner initializes at most three browser sessions at once, with one
initializer per registered user. A startup slot is released after the SDK mount
completes; its independent context and conversation continue under the existing
per-user conversation cap. Queueing still consumes the overall run deadline.
The conversation lease and answer deadline start after a startup slot is acquired.
No prompt or embed request is retried on initialization failure, and uncertainty
still halts the shared authorization.

Each qualification wave waits for every intended session to mount before its
first prompt. The receipt records `initialized_together` and requires the full
wave count, including fifteen sessions in the five-per-user wave. A failed mount
aborts that barrier and halts the run. Assessment batches do not use the barrier.

In the 26 October 2026 BackBond sandbox diagnostic on the 8-GiB worker, six
parallel initializations failed at the SDK mount, while all six serialized
initializations completed. The diagnostic sent zero prompts and wrote no ledger
entries. This supports pacing initialization; it does not qualify fifteen active
conversations or the customer assessment. Repeat the complete bounded readiness
and concurrency qualification on the merged image before any assessment. Preserve
failed-attempt receipts and charged reservations; never clear a halted ledger or
replay an uncertain request solely because provider logs contain no match.

## Customer selection and acceptance

AllCloud is the prospective pilot partner. No customer, account or authorization
has been supplied. Select one customer and one existing Quick Chat agent first.
Customer location in Israel does not identify the agent's AWS Region. AWS's
[current region table](https://docs.aws.amazon.com/quick/latest/userguide/regions.html)
lists Tel Aviv (`il-central-1`) without agentic features as of 25 October 2026.
The dated commercial-region allowlist in `quick_pilot_operations.py` is a
qualification gate, not an evergreen AWS service claim. Refresh it against AWS
before adding a region.

Confirm the account's authentication before setup. This release uses registered-user
embedding. IAM Identity Center deployments requiring identity-enhanced sessions
and trusted identity propagation need another implementation and live qualification.
Do not bypass the customer's authentication or migrate their account to fit this
connector. See [AWS identity embedding](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_GenerateEmbedUrlForRegisteredUserWithIdentity.html).

The customer must authorize the target, identities, synthetic sources, scenario
families, call caps, time window, execution account/Region, evaluator and evidence
handling. AllCloud's interest is not authorization from a customer's administrator.

## Access package

Use `deploy/amazon-quick/customer-access.template.json` as a reviewable optional
CloudFormation template for **BackBond AWS execution**. It creates one IAM role;
it does not enroll Quick, add licenses, create users or configure log delivery.
Review all parameters with the customer, deploy in the Quick/log-group Region,
and acknowledge `CAPABILITY_IAM`. No credentials belong in template parameters.
Do not deploy with dummy account/user ARNs. This template has not been applied
to a customer account.

The runtime policy permits exactly three registered user ARNs to generate embed
URLs for one HTTPS origin, and `logs:FilterLogEvents` on one log group. Trust is
restricted to one exact BackBond role and a unique ExternalId, with one-hour
maximum sessions. Required `PilotExpiresAt` limits both new assumptions and the
two runtime API grants through `aws:CurrentTime` conditions. BackBond's worker
role also needs `sts:AssumeRole` limited to
that exact customer role. The existing sandbox worker role does not have it yet.
The worker's SSM management and private evidence storage permissions are separate
from these customer permissions. Review them independently.

The customer administrator handles user licenses, Viewer sharing, source grants,
structured workforce RLS and log delivery. Use three dedicated identities:
employee, Sales manager and HR, with grants described in
[the profile runbook](internal-agent-knowledge-pilot.md). Synthetic data stays in a
dedicated test area. Keep writes/actions disabled or sandboxed. Do not index the
unfiltered workforce CSV as a shared document. Exported answer oracles/profile
JSON must never be indexed in the agent.

`CHAT_LOGS` delivery must start before qualification and is not retroactive.
Account/Region delivery can contain other users' conversations. Prefer a sandbox
or reviewed filtered pilot destination that preserves original synthetic input,
output, identity and timing. IAM access to a shared log group exposes its records;
a query filter is not a security boundary. Customer-admin setup uses separate
delivery/KMS permissions; the scan role does not need Quick administration.

Use a profile in a private `/pilot/config/aws-config` file:

```ini
[profile customer-quick-pilot]
role_arn = arn:aws:iam::CUSTOMER_ACCOUNT:role/EXACT_CUSTOMER_ROLE
credential_source = Ec2InstanceMetadata
external_id = UNIQUE_CUSTOMER_EXTERNAL_ID
role_session_name = backbond-quick-pilot
duration_seconds = 3600
region = CUSTOMER_QUICK_REGION
```

`credential_source=Ec2InstanceMetadata` is for an EC2 worker with an instance role.
Set `aws_profile` to `customer-quick-pilot` in all three registrations. For a
customer-owned worker, review its instance role and use the appropriate local
temporary-credential chain. Never copy long-lived AWS keys into the bundle,
runtime env file, image, tickets or public embedding host. Validate role trust
accepts the correct ExternalId and denies an incorrect/missing one before tests.

## Build and verify the complete scoped runtime

```sh
python -m tools.build_amazon_quick_worker --output /secure/build/quick-pilot-context
docker build --build-arg SOURCE_REVISION=REVIEWED_FULL_COMMIT --tag backbond-quick-customer-pilot:REVIEWED_TAG /secure/build/quick-pilot-context
```

The allowlisted build context includes the exact profile/grader import closure
and source-file hashes. It excludes local state, environment files, AWS config,
transcripts and answer-oracle installation outputs. Review `worker-source.json`;
release candidates require `working_tree_dirty: false`. The build runs three
offline capture/grading/report fixtures with network disabled in Python. These
exercise the packaged path, not live judge accuracy or AWS permission behavior.

Build on Linux x86_64 with CPython 3.11, matching the hash-locked runtime. The
host must have Docker and official AWS CLI v2 installed at `/usr/local/aws-cli`.
AWS CLI is mounted read-only; no unpinned CLI download is hidden in the image.
The image is non-root and exposes no service. Before use, schedule and verify
the worker's bounded automatic stop, check the agreed spending limit and retain
an explicit operator stop step. Use encrypted disk, IMDSv2, no inbound security
group rules and reviewed outbound routes. No CI routing changes are needed;
eligible heavy checks remain on the existing isolated VM.

Create one private customer root with `config`, `state` and `evidence` directories,
owned by UID 10001 and accessible only to the operator and runtime. Reuse the
same state directory across restarts. Mount config read-only. `runtime.env`
contains only pilot tokens plus explicitly approved evaluator credentials, if
used. Keep it mode 0600. Paths inside files use `/pilot/config`, `/pilot/state`
and `/pilot/evidence`.

```sh
export QUICK_PILOT_ROOT=/secure/customers/CUSTOMER_PILOT
export QUICK_PILOT_IMAGE=backbond-quick-customer-pilot:REVIEWED_TAG
bash deploy/amazon-quick/run-worker.sh
```

The default performs `runtime-check`: Linux/architecture, Python, non-root,
AWS CLI v2 and a no-network Chromium smoke check. It sends no target or judge
requests. `runtime_verified` is required before live qualification. A local
Windows fixture/import check cannot substitute for this check.

## Export, install, qualify and preflight

```sh
bash deploy/amazon-quick/run-worker.sh tools.internal_agent_pilot --export --output /pilot/evidence/setup-export
bash deploy/amazon-quick/run-worker.sh tools.quick_pilot_operations processing-example
```

Complete the exported setup file and processing scope in private config files.
Install only `setup-export/sources/*`, preserving the profile's ACL/RLS boundaries.
Record actual resource IDs and administrator evidence. Set approvals only after
customer review. The processing scope records the actual region/authentication,
execution location, authorization/processing references, retention and start of
the qualification window. Defaults refuse external grading.

Register three exact endpoints on the approved static HTTPS host (distinct paths
per identity), random pilot tokens and their hashes, exact user/agent/account
bindings and browser-host allowlists. Existing registrations and durable budgets
must not be reset. Each new identity needs ten readiness calls plus assessment:
employee **22** total (10+12), Sales manager **12** (10+2), HR **14** (10+4), before
any separately approved manual checks. Total planned submissions: **48**. These
are caps, not a commitment to send all calls after an error.

Run the ten-marker command once for each identity, using its token env name and
unique receipt path, e.g.:

```sh
bash deploy/amazon-quick/run-worker.sh tools.amazon_quick_pilot --live --endpoint https://AGREED_HOST/employee/index.html --token-env QUICK_EMPLOYEE_PILOT_TOKEN --receipt /pilot/evidence/readiness-employee.json
bash deploy/amazon-quick/run-worker.sh tools.quick_pilot_operations preflight --config /pilot/config/setup.json --scope /pilot/config/processing.json
```

The new readiness receipt binds to the exact user, endpoint, account, agent,
Region, log group and pilot ID. Older account/agent-only receipts cannot qualify
the three identities. Preflight reads the ledger without changing it, refuses
halts/in-flight submissions and busy leases, verifies preserved budgets and all
three fresh readiness receipts, and enforces region/authentication/data rules.
It makes no AWS, browser or judge calls. Setup ACL/RLS correctness remains an
administrator attestation; it is tested behaviorally during the assessment.

## Capture, grade and report

Default capture has **no external evaluator**:

```sh
bash deploy/amazon-quick/run-worker.sh tools.quick_pilot_run --config /pilot/config/setup.json --scope /pilot/config/processing.json --output /pilot/evidence/run-01
```

This gates the same 18-case driver on processing scope and readiness, captures
once and renders `report.md` from `run.json`. Unknown submissions stop; never
automatically rerun the whole command. Error or incomplete runs return nonzero
and preserve evidence. Exact disclosures can fail without a judge; semantic
cases remain inconclusive for analyst review. A capture-only run is not a fully
graded assessment. This release does not implement AWS-only semantic judging.

For automated grading, the customer must explicitly permit OpenRouter in both
setup and processing scope. Mount the key privately, add `--judge-openrouter`
and `--judge-budget-db /pilot/state/evaluator.sqlite3`. Agree the evaluator
limit first and retain the database/scope across regrades. Relevant prompts,
answers and synthetic source context go to OpenRouter. Execution in AWS does
not keep those data in AWS. Customer-account-only processing is not established
by running in BackBond's AWS account.

For missing source/version or injection-exposure proof, obtain the exact
message-bound administrator/provider evidence described in the profile runbook.
Regrade the saved run with the existing driver; do not resubmit target prompts.
Render the new artifact with `tools.quick_pilot_report --run ... --output ...`.
Review failures with AllCloud/customer, attach the evidence, propose a concrete
fix and a separately bounded retest. No pass rate or BackBond Score is issued
for absent evidence. The report contains verdicts and limitations, not raw
transcripts; it is Markdown source for review, not a branded final PDF.

## Exit and release gates

Preserve private run, receipts, source manifest/image digest and call/evaluator
ledgers. Stop and verify the worker. At the agreed end revoke the customer trust
and user/resource grants, remove test sources, and delete evidence after the
agreed retention period. CloudFormation role creation does not auto-expire;
session duration and registration expiry do not delete an IAM role or stop an EC2
worker or revoke issued Quick browser sessions. The template cuts off its API
grants and new assumptions at `PilotExpiresAt`; retain explicit worker/session
closure and cleanup. Record each action with the customer administrator.

Before any customer claim of readiness: source review and required CI passed;
merged/pinned candidate identified; complete Linux image built and runtime check
passed; actual customer identity/region/logging/setup qualified; processing and
cost boundaries approved. The first real pilot additionally needs a reviewed
graded report or explicitly limited capture/analyst-review agreement. None of
these gates is satisfied by scripted fixture results alone.
