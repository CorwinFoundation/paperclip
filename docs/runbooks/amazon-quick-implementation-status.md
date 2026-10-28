# Amazon Quick implementation and qualification matrix

BackBond | Internal operating record | 28 October 2026

This distinguishes implemented code, offline verification and actual AWS
qualification. A completed component does not authorize a customer scan or make
every configuration qualified. The supported pilot route is an operator-led,
synthetic configuration copy in BackBond AWS. BackBond Score remains N/A.

| Implementation | Code and offline checks | Actual qualification / remaining gate |
|---|---|---|
| Registered-user chat transport | Single and concurrent runners; fresh context/conversation per case; durable shared accounting; halt on uncertain submission; native log authority. | BackBond synthetic HR/finance capture accepted across three retained epochs. No production/customer deployment has been qualified. |
| HR/finance profile | 600-case profile, fixture exports, permissions/RLS specification, readiness/spike, grading and report. | 600 initial cases / 670 conversations / 790 turns captured. All 600 remain inconclusive: 461 semantic review, 135 missing exact source identity, four empty replies. |
| Healthcare profile | Separate 600-case profile, synthetic patients/staff, three roles, two structured row-rule sets and role-scoped sources. Shared profile/grading/preflight checks also run against this variant. | No live healthcare run. No real patient/staff data, clinical guidance or PHI handling is authorized. Needs its own setup, source/role checks, readiness, concurrency and bounded capture release. |
| Configuration copies | Default prompts, persona/tone/style/length-only agents and custom-instruction agents. Exact synthetic space mapping; actions disabled; account-specific model/subscription identifiers excluded. | Offline administrator-readback comparison binds ACTIVE/PUBLISHED copy, request/export hashes, prompt text and source mapping. Each customer must approve and attest to its own copy, grants and model differences. |
| Uploaded files and knowledge bases | Installation distinguishes role-scoped spaces from separately shared KBs. Native S3 one-object source template and permission cutoff. | Synthetic strategy S3 ingestion repaired and exercised. Exact source/version exposure is still absent in 135 baseline cases; a space citation or display name cannot substitute. Document-level ACL behavior needs separate qualification. |
| Structured data / RLS | HR workforce dataset; healthcare patient and staff dataset definitions and explicit role rules. Unfiltered CSVs must not be uploaded as shared documents. | Separate 18-case pilot preserved intended restricted-value separation in its sampled probes, but observed authorized HR utility inconsistency and ambiguous visibility wording. Production RLS and a backend root cause are not established. |
| Browser pacing and reuse | Persistent Chromium, isolated contexts, bounded recycling, startup pacing, optional DOM pacing and hash mismatch checks with native-log fallback. | 90-opening/two-lifecycle diagnostic and 18-session maintenance passed. Not a two-hour SLA or proof of uninterrupted full-pack recovery. |
| Worker, access and logs | Scoped execution/runtime roles, isolated Lambda-forwarded logs, no inbound ports, non-root scanner, IMDSv2, private evidence and stop timer. | Actual narrow management role delivered a zero-prompt command and denied both tested Parameter Store reads. Worker stopped/Standard; six grant cutoffs expired; three added users removed; unrelated users/subscription preserved. |
| Operator automation | Existing reviewed commands and manual control-plane workflow. [PR #1292](https://github.com/CorwinFoundation/are-scanner/pull/1292) is a separate draft checkpoint for offline state/template preparation. | Full unattended deployment, live dispatch and automated closeout are not connected or qualified by that draft. A queue acknowledgment is not AWS execution. Do not merge or advertise it as a complete operator pipeline. |
| Hostinger job dispatch | Not implemented for this route. | Explicit v1.2 deferral; a separate integration/release is required. |
| Customer-account worker | Not qualified. | Explicit v1.2 deferral; no claim that execution/evidence stay wholly inside the customer's AWS account. |
| Production native-user path A | The registered-user transport is reusable where exact permissions and customer scope allow it. The 600-case scope currently requires Path C. | No customer production-account assessment authorized or qualified. Do not relabel a configuration-copy report as production evidence. |
| Identity Center path B | No identity-enhanced token/session implementation. | Conditional work only if A/C are refused; requires tenant-specific identity design, authorization and live qualification. No device/conditional-access bypass. |
| Actions, Automate, Desktop, recurring scans | Not provided by this chat transport/release. | Separate scope and evidence needed; no action-level or coverage decision. |
| Semantic review and rating | Deterministic checks preserve failures where evidence supports them; missing semantic/retrieval evidence stays inconclusive. External grading defaults off. | No conclusive automated rating from the baseline. A qualified approved review route is needed; do not change saved verdicts to hide the gap. |
| Retention | Thirty-day default, owner/inventory and closeout procedure. | Current cohort fixed deadline 24 November 2026 at 17:14:49Z. No independently verified active deletion scheduler or future deletion. Expiry/stopping is not deletion proof. |

## Evidence and interpretation

The dated [capture and operational closeout receipt](../evidence/amazon-quick/2026-10-28-synthetic-capture-closeout.json)
contains aggregate counts and source/receipt hashes, with no transcripts,
credentials or private oracle. The recorded source/image applies to the accepted
baseline, not automatically to later changes. New customer releases need a
reviewed source/image and actual setup/readiness evidence.

The separate 18-case utility observation is not part of the 600 case rows. Two
authorized HR requests were refused while another request under the same
verified identity returned the synthetic payroll record. The cause is unresolved;
it does not prove an ACL backend defect. A Sales reply described restricted
Engineering rows as absent without disclosing a protected value in that sample.
Its 48 calls are already included in the internal call accounting.

## Customer-specific completion checklist

1. Nominate customer, administrator/security owner, actual account/region,
   authentication method, agent, use case and working languages.
2. Select the supported identity/execution path and profile. Approve the
   sanitized configuration, source/role matrix, processing locations/readers,
   call/cost budget, access window and deletion owner/deadline.
3. Prepare and attest to the copy using the
   [administrator guide](amazon-quick-path-c-admin.md). Review any canary
   instrumentation as a configuration difference; default prompts do not prove
   an instruction canary was installed. Keep private profiles/oracles out of
   Quick and partner packs.
4. Verify native ingestion and indexed versions, each identity's useful allowed
   tasks and denied tasks, exact source identity and row-rule behavior. A refusal
   of an allowed task remains a utility gap even when transport succeeds.
5. Qualify the pinned worker, stop timer, permissions, native logs and durable
   budgets, then readiness/spike/capture. Preserve all prior charges and unknown
   submissions. No automatic retry or reuse of the closed baseline grants.
6. Report supported observations and gaps; separately approve/qualify any
   semantic review. Stop, revoke access, remove added users, preserve unrelated
   resources and verify scoped deletion at the agreed deadline.

No customer is nominated or authorized by this matrix. Neither an Israeli
customer's location nor a successful transport test authorizes us-east-1
processing, external evidence review, production claims or a numeric rating.
