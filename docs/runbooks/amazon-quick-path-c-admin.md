# Amazon Quick Path C: administrator checklist

BackBond draft | 26 October 2026 | Configuration copy over synthetic sources

Path C is the selected route. The assessment runs against a recreated Quick
agent in a separate sandbox AWS account with registered native/IAM-backed test
users. It does not test production grants, production row rules, Identity Center,
trusted-device rules or real connected data. A sandbox copy is not a workaround
for customer security policy: the customer must approve the export and sandbox.

## 1. Intake and authorization

- Identify the customer, AllCloud technical lead, AWS/Quick administrator and,
  for healthcare, information-security/privacy owner. The hospital mentioned in
  the design is a candidate until AllCloud confirms it.
- Record production and sandbox account IDs, actual Quick region, agent IDs,
  authentication/provisioning mode, execution/evidence location and named readers.
  The selected sandbox must support registered-user Quick Chat embedding.
- Check current [AWS regional availability](https://docs.aws.amazon.com/quick/latest/userguide/regions.html).
  Location in Israel does not establish AWS region or data residency. The dated
  25 October check found no agentic features in il-central-1; recheck at intake.
- Approve synthetic-only scope, tested roles, source/permission matrix, test
  families, UTC window, target-call and cost caps, 30-day default retention and
  closeout. External grading stays off unless explicitly approved in writing.
- Record a reviewed source revision/image and completed required CI before live
  qualification. There is no promised onboarding time or two-hour scan SLA yet.

## 2. Export and review the source configuration

The customer's administrator runs the supported API in its own environment:

```sh
aws quicksight describe-agent --region REGION --aws-account-id SOURCE_ACCOUNT --agent-id SOURCE_AGENT --output json > source-agent.private.json
```

No production login, AWS key, employee session cookie or IdP exception is handed
to BackBond. `DescribeAgent` exposes configuration metadata, not a dataset export;
free-text instructions/prompts can still contain secrets or personal information.
Review and sanitize them before transfer to the agreed private destination.
Do not export patient/staff documents, connector credentials or live source data.

Record the export hash, reviewer's name, authorization reference and permitted
redactions. Map every production space to a new synthetic sandbox space. Real
action connectors remain disabled. Review model-profile equivalence manually;
account-specific subscription/model identifiers are not automatically copied.

The private operator tool prepares a CreateAgent request and difference receipt:

```sh
python -m tools.quick_configuration_copy --export source-agent.private.json --review copy-review.private.json --output copy-plan
```

It deploys nothing. The review file must bind the exact export hash and approve
synthetic replacements. An administrator creates the sandbox agent using the
reviewed request, then attests to the resulting configuration. Preserve source
instructions except approved redactions/instrumentation; record every difference.
The supplied baseline instructions are for BackBond's own synthetic baseline,
not a replacement for the customer's instructions. Any canary added to copied
instructions is measurement instrumentation and must be disclosed in the receipt.

## 3. Install synthetic users, sources and access

Create three dedicated chat-capable test users. Give them Viewer access to the
same sandbox agent; do not share them with humans. For HR/finance use employee,
Sales manager and HR. For healthcare use ward clinician, operations and HR/payroll.
Map the template's three user parameters to these roles in order.

Use distinct controlled email addresses for the three users. Quick resolves
document-level ACL identities case-insensitively within the creator's namespace;
shared emails can deny document access to every user with that address. Record
the actual bindings before the first conversation or ACL refresh. Separate
Space/KB sharing does not establish document-level ACL coverage. ACL support is
fixed at KB creation, so qualify an ACL-aware KB separately when that control
is in the customer scope. See [AWS ACL identity guidance](https://docs.aws.amazon.com/quick/latest/userguide/acl-best-practices-kb.html).

Install only the exported `sources/` files, following `installation.json` hashes,
source grants and structured row rules. Keep the profile, expected answers,
scanner code and private operator kit outside Quick's indexes.

Files uploaded directly into a space inherit that space's access. Use separate
role-scoped spaces for restricted files; do not put payroll in a space shared
with everyone. Install roster CSVs as structured datasets/topics with actual
row rules, and strategy/quality-review through separately shared knowledge bases.
See [AWS space permissions](https://docs.aws.amazon.com/quick/latest/userguide/working-with-spaces.html).

Use Quick's native **Can manage** and **Can view** sharing groups, then inspect
`DescribeSpacePermissions`. A five-action metadata owner and a
`DescribeSpace`-only viewer are insufficient for uploaded files. On the 25 October
sandbox, native Can manage included `CreateDocument`, `DeleteDocument`,
`GetDocument`, `ListDocument` and the space-folder actions in addition to the
five space-management actions. Native Can view included `DescribeSpace`,
`GetDocument`, `ListDocument`, `GetArtifact`, `ListArtifactVersions` and
`ListSpaceFolderMembers`. Record the actual actions and exact principal ARNs;
never give a tested reader owner access to fix an upload or retrieval problem.

Exported source hashes bind the raw UTF-8 bytes, including LF newlines. Verify
each local file against `installation.json` before upload on Windows as well as
Linux. The r2 setup package corrects a Windows CRLF translation issue in the
earlier package; do not mix those versions. Confirm every file reaches Ready
and save its Quick file ID. Ready establishes indexing status, not proof that
the agent retrieved a particular injected passage.

Keep S3 ingestion separate from scanner code and evidence. The synthetic-source
template creates a private, versioned, encrypted fixture bucket and an expiring
read-only Quick role. Object reads stay under `fixtures/*`. Its exact-bucket
`ListBucket` permission also supports requests with no prefix so Quick can
validate the bucket; this successfully resolved the knowledge-base connection's
bucket-access error in the sandbox. Never reuse this fixture-only policy for a
bucket that also holds customer evidence or other private objects.

Use `synthetic-knowledge-source.template.json` for each separately shared S3
knowledge base. It creates a dedicated private bucket and grants the explicitly
reviewed existing Quick role read of one exact object, plus bucket listing,
until the pilot cutoff. It changes no trust policy. Upload only that fixture;
keep dataset manifests, other fixtures, scanner code and evidence elsewhere.
On 26 October, a controlled CloudTrail trace showed Quick listing the bucket
without a prefix and then issuing `HeadObject` for a manifest outside the KB's
selected prefix. The single-file grant correctly denied it and the sync failed.
An include prefix therefore cannot replace isolation at the S3 bucket boundary.
Retain the trace and require successful ingestion after moving the same bytes.

Inspect the existing Quick role with `GetRole` and record its actual ARN. If
native bucket validation is disabled, an administrator can select **Use an
existing role** with that same ARN in Quick's AWS resources screen. Preserve
existing account permissions; do not overwrite another account's canonical S3
managed policy or grant bucket-wide reads to fix this error. Native identity
and bucket Deny policies keep the exact object and original expiry. The bucket
is versioned and retained, so the agreed closeout must erase all versions.

For S3 knowledge bases, set `S3KnowledgeBaseParameters.BucketUrl` to the bucket
root. Put the document prefix in the knowledge base template's
`filterConfiguration.inclusionPrefixes`. A path appended to the bucket URL can
be interpreted as `MetadataFilesLocation`; use that field only for actual
per-document metadata, not the documents themselves. Inspect the saved
`DescribeDataSource` parameters and the completed sync result. Remove any
default recurring sync schedule for a bounded pilot.

Use the native knowledge-base editor to save and verify the complete S3
configuration before qualification. In this sandbox, the minimal API template
was accepted but the editor failed on a missing `deletionProtectionConfiguration`.
After repair, the native editor saved explicit file filters/size limits and
deletion protection. An accepted API request or an ACTIVE knowledge base does
not prove that a document has ingested; require a completed sync with the
expected document count and separately verify the saved HR-only permissions.

After changing the source, inspect the native sync history as well as the API.
In the 26 October sandbox comparison, the API configuration update reported
RUNNING while the native history still listed the earlier failed attempts.
The configuration-triggered full crawl and one explicit native sync both later
completed. The downloaded native report showed one available `strategy.txt` and
zero unavailable items. Save the completed ingestion IDs and downloaded report;
the API request receipt alone is not that evidence. Do not repeat or cancel a
live source sync merely because an observation times out. Keep any default
recurring schedule disabled for the bounded pilot.

- HR/finance: payroll and strategy are HR-only; Sales manager sees only Sales
  workforce rows. Configure the strategy knowledge base separately from agent
  sharing. An unrestricted CSV document is not row-level security.
- Healthcare: every file/row is labelled synthetic, with SYN-prefixed patient
  and staff identifiers. Ward clinician sees Ward-A patient rows and approved
  clinical simulation documents; operations and HR/payroll see no patient rows.
  HR/payroll alone sees staff compensation. Configure the restricted synthetic
  quality-review knowledge base separately from agent access.
- Use structured datasets/topics for CSV sources. Record actual source resource
  IDs, indexed versions, sync evidence and the administrator-verified grants/RLS.
  A display name alone does not establish exact source identity or attack exposure.

Use the customer stack `customer-pilot-v12.template.json`. BackBond first supplies
one dedicated execution-role ARN and unique ExternalId. The customer role grants
embedding for exactly three users on one origin and FilterLogEvents for one group,
with a UTC cutoff. BackBond's execution role then receives AssumeRole permission
for that exact customer role. Separate workers/volumes and execution roles are
required per customer; a shared parent role must not reach every customer.

Give each identity a distinct, real HTTPS page URL on the approved embedding
origin. The target registry requires exactly one registration per endpoint URL,
before checking its bearer token. Three registrations cannot share one page URL.
Byte-identical static page copies with relative SDK/script links can provide the
three URLs without changing the origin grant. Verify each returns the reviewed
bytes over HTTPS. Keep registry files, tokens, source code and evidence outside
the public hosting prefix; the public pages contain no credentials.

## 4. Configure and verify logging

Use the documented `CHAT_LOGS` field **`user_arn`**, per the
[AWS schema](https://docs.aws.amazon.com/quick/latest/userguide/monitoring-cloudwatch-logs.html).
The initial 25 October check read an empty first page. The complete four-page
audit found 20 retained provider records and verified this field. Always follow
pagination tokens, including after empty pages; qualify fresh identities separately.
Delivery is regional and not retroactive.

In a dedicated synthetic sandbox, a vended-log delivery to a dedicated pilot
group may be appropriate. A second destination is supported by the general
CloudWatch delivery model, but Quick/account-specific behavior still needs a
live check. Direct Quick delivery is account-wide, not a three-user filter.

When filtering an existing shared group, select `forward_existing`: a subscription
filter invokes a small customer-owned Lambda, which re-emits only the three
registered users' records for the agreed agent. Its role reads nothing and writes
only to the pilot group. Original provider message bytes are preserved. Source
and destination groups must differ. This adds a function and its role in the
sandbox; do not describe the deployment as only an IAM role.

Before scanning, record the deployed filter/function configuration and an
unfiltered, untruncated pilot-group audit spanning qualification. The observed
user set must be exactly the three identities and the agent must match. One
sample or a query with a user filter cannot prove isolation. The BackBond runtime
must not read the original shared group.

The Lambda template reserves two concurrent executions by default. New AWS
accounts may lack room above the minimum unreserved pool. Review the account
quota first; set `ForwarderReservedConcurrency=0` to use existing unreserved
capacity if necessary. Record that choice and the actual account quota in the
deployment receipt. Do not request a quota increase silently.

## 5. Qualify and run

The host AWS CLI binaries must be readable/executable by scanner UID 10001;
an installer run under `umask 077` can leave `/usr/local/aws-cli` root-only.
Keep credentials/configuration outside that vendor directory. The worker launch
script uses a private memory-only home for the AWS CLI's temporary STS cache,
while keeping the container root filesystem read-only. Qualify both SDK and CLI
access through the actual runtime role, including denial of the shared source
log group and scanner source bucket; a successful `aws --version` alone is
insufficient.

For each exact identity/registration, run three benign marker checks. Keep the
fresh bound receipt. The earlier ten-check platform receipt is separate evidence
and cannot qualify a different user. Run the bounded concurrency spike on the
reviewed sandbox release before selecting concurrency for the baseline.
The 44-call spike exercises all three identities at concurrency 1, 2, 3 and 5,
including fifteen simultaneous cases and a repeat wave, within 15 minutes.
Require a fresh receipt bound to the exact identities, target and registrations;
a result from one identity or another agent does not qualify this setup.

The 600-row pack contains paired variants and multi-turn cases: **790 initial
target calls**, plus nine readiness calls and ten platform checks. Only initial
failures receive two fresh confirmation attempts. With external grading off,
the reviewed HR/finance grader permits at most 400 initially failing cases,
containing 590 turns per confirmation. The baseline maximum is therefore
**1,970 calls**, retaining all initial cases and both confirmations. Adding
ten platform, nine identity and 44 spike calls gives **2,033 new v1.2 calls**.
This bound depends on the pinned grader/profile/capture/qualification hashes;
rederive it before changing those inputs or enabling external grading.

The hard allocation is **2,426 v1.2 + 48 separate follow-on + 26 retained legacy
= 2,500 calls**, within the approved BackBond baseline's USD 200 incremental
ceiling. The 26 October recovery started with 32 charged reservations retained.
Adding its 2,033 fresh-call bound gives 2,065 cumulative v1.2 calls, or 2,139
combined, leaving 361 calls within the allocation. These are pre-run bounds,
not observed usage or permission to start another fresh run. Each customer
needs its own reviewed cost/call plan and preserved consumption. The follow-on
uses separate immutable authorization and pilot IDs, sequentially,
with the same persistent ledger and identity leases. Every manual, readiness,
uncertain submission and confirmation consumes the preserved allocation.

Unknown submissions halt; no automatic resend or ledger reset. Browser capture
is only pacing/progress. Correlated AWS logs finalize answers, and a captured
text/provider hash mismatch cannot pass. Preserve private checkpoints throughout.

Review proposed fixes with AllCloud and the customer. The report must say
configuration copy, show inconclusive/unrun cases, separate permitted utility,
and keep BackBond Score N/A. Record remediation owners and scope effort after
evidence review; do not invent fix hours or claim production root causes.

## Screenshots and evidence to attach before release

- [ ] Sandbox account/region and native registered test identities.
- [ ] Agent configuration and each disclosed difference from the reviewed export.
- [ ] Source/space/knowledge-base grants and indexed source versions.
- [ ] Structured patient/workforce/staff row rules for each tested role.
- [ ] Dedicated log group, delivery or subscription/function configuration.
- [ ] Three-user/one-agent unfiltered audit and fresh readiness receipts.
- [ ] Live forwarder qualification and concurrency receipt for the pinned release.

This checklist has not yet been exercised end-to-end for v1.2. Attach actual
BackBond sandbox evidence first; never fabricate screenshots or treat unit mocks
as AWS deployment proof.
