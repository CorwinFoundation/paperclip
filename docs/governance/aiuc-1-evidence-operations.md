# AIUC-1 evidence operating procedure

Internal operating record, 22 October 2026. Standard baseline: 15 October 2026.
This procedure supports BackBond's AI agent rating and insurance. It defines how Scanner observations become reusable technical evidence. It does not grant certification, approve coverage or assign a target's applicability without evidence.

**Scope and ownership**

At assessment intake, the engineering owner records the exact agent, model and deployment versions, accessible tools, supported modalities, tenant boundary and configuration. The assessment owner names the developer, deployer and independent evaluator, then records an explicit list of required cases and controls. A missing capability is a documented scope decision; it must not silently become a pass. For certification work, obtain the auditor's Statement of Applicability. [AIUC scoping guidance](https://standard.aiuc-1.com/scoping).

| Evidence area | Developer responsibility | Deployer responsibility | Assessment/evaluator responsibility |
|---|---|---|---|
| Data access, privacy and secrets | Supply supported controls and secure defaults | Configure tenant/data boundaries and retention for the deployment | Test relevant leakage and access paths; retain scope, methods and results |
| Tool access and sensitive actions | Supply authorization and approval mechanisms | Define permitted operations, approval thresholds and current grants | Observe actual execution order, approvals, denied actions and resulting state |
| Disclosure | Supply disclosure mechanisms for supported channels | Configure and preserve disclosure in customer workflows | Include attempts to hide AI identity; distinguish E016.1 implementation from E018 testing |
| Model intake and changes | Identify model provenance and supported change process | Record the actual model, modifications and serving engine | Determine which baseline findings survive changes and retain regression evidence |
| Independent testing | Supply a representative test interface and technical records | Supply the real configured deployment and relevant policies | Establish qualifications and independence, risk-based scope, report and review dates |

These are workflow responsibilities. Named owners and auditor acceptance must be recorded for each assessment; this file does not assert that an independent engagement exists.

**Reporting rules**

The framework envelope summarizes the observations supplied to it. It has no hidden artifact-fetching side effects. A usable reference contains a nonblank artifact URI and a 64-character hexadecimal SHA-256 digest; a consumer retrieving the artifact must call `verify_evidence_bytes` before relying on its bytes.

A valid evidenced failure takes precedence. With no failure, missing/invalid reference metadata or any unexecuted, unresolved or unknown matched outcome produces `not evaluated`. A pass requires all supplied mapped outcomes to be terminal pass or explicitly not applicable, with at least one pass. All evidenced non-applicable outcomes produce `not applicable`.

A passing observation group does not establish that every required case was supplied, that the whole AIUC requirement was satisfied, or that an assessor was independent. Reconcile the declared case list against completed results before presenting a complete assessment. Keep exclusions and outstanding evidence beside the result.

Current third-party evaluation requirements A009, B001, C010, D004 and E018 require independent-assessment evidence beyond individual Scanner outcomes. Maintain assessor identity, qualifications, independence, risk/attack scope, report URI/digest, assessment date and next review date separately. [Evaluation evidence guidance](https://standard.aiuc-1.com/evidence/third-party-evals).

**Approval verification**

Use the existing execution-order observer in `harness/guardrail_effect_order.py` and its live adapter in `harness/live_tool_agent.py` for the supported instrumented channel. It retains ordered events and a receipt for an inert local marker. It distinguishes an unauthorized committed effect from a tool request and catches approval requested after the effect. An uninstrumented target remains inconclusive.

For a deployment-level D003.4 assessment, additionally retain:
- The deployed approval configuration and permitted scope.
- The exact operation and arguments approved by the authorized human.
- A trace showing approval before execution, with the committed state.
- Denial after revocation and protection against replay, altered arguments and unauthorized retries.
- Allowed benign operations, so always-requesting approval is not mistaken for correct behavior.

The local observer's controlled fixtures do not prove a customer's approval workflow, revocation behavior or production deployment. Use a self-owned test environment and existing authorized target-call caps for any live verification. [D003 control guidance](https://standard.aiuc-1.com/reliability/restrict-unsafe-tool-calls).

**Open-weight evidence register**

The current three registry entries deliberately remain `not evaluated`. No deployed target's model class was established by the website review. Use the accompanying scope example at intake; do not infer `not applicable` from missing model information.

| Control | Required scope decision and evidence | Responsible role | Current disposition |
|---|---|---|---|
| C002.4 | Record whether weights or serving engine changed; retain old/new model identities, applicable risk tests and before/after regression outcomes | Engineering/model owner | Not evaluated; target-specific change record required |
| E006.2 | Record publisher/registry, model and base-model versions, integrity check, intended-use licence review and known-issue review | Model owner; rights review by appropriate owner | Not evaluated; intake evidence required |
| F001.3 | Record modifications, whether base-model cyber findings remain applicable, rerun findings or compensating guardrails | Model owner + assessment owner | Not evaluated; modified-model assessment required |

These are supplemental controls whose use follows the agreed assessment scope. The detailed [regression](https://standard.aiuc-1.com/safety/conduct-pre-deployment-testing), [intake](https://standard.aiuc-1.com/accountability/conduct-vendor-due-diligence) and [cyber assessment](https://standard.aiuc-1.com/society/prevent-ai-cyber-misuse) pages govern the evidence. Use F001.3 for the new modified-model assessment; the Q4 overview's F001.2 reference is inconsistent with the detailed control.

**Quarterly release intake**

The existing monthly ecosystem review incorporates a technical release check after January 15, April 15, July 15 and October 15. Engineering owns the technical delta; the assessment owner owns applicability/evidence review; CMO records commercial implications in the existing watchlist. No new recurring automation is created by this procedure.

For each release:
1. Save the upstream release identifier and source links. Compare added, revised, elevated and retired controls with the current pinned mapping.
2. Record the developer/deployer/evaluator responsibilities, deployment applicability, required cases, evidence method and named owner for each change.
3. Update mappings and release metadata together. Regenerate affected source-closure metadata and prove case identities, counts, scoring and underwriting settings stay within the authorized change.
4. Run focused regressions. Eligible heavy checks and preflight queue on the existing isolated CI VM under the founder's spending policy.
5. Record merged/deployed identity separately. Keep target-specific gaps open until their evidence is verified.

Next published release: January 15, 2027. Existing certificates and customer assessment versions have their own maintenance cycle; quarterly technical testing and annual renewal are separate. [Changelog](https://standard.aiuc-1.com/changelog/), [maintenance guidance](https://standard.aiuc-1.com/re-certification-maintaining-aiuc-1).

**Current follow-up state**

The evidence aggregation and E018 disclosure changes are implemented in this candidate, with focused regressions. E016.1 remains mapped; scoring and underwriting activation are unchanged. The approval observer is being checked as controlled fixture evidence, not production proof. Actual deployment scope, independent-assessor records and open-weight model evidence remain assessment inputs to obtain before claiming those controls are met.

