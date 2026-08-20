# Scanner Implementation Supervision Contract (v2.1)

**Parties:** Scanner Engineer (operational seat) ↔ CTO (strategic seat); Underwriter (Head of UW, co-counterparty on `contracts/are-scanner-api-v1.md`); Auditor (audits the supervision log).

**Status:** Locked at v2.1 on 2026-08-20. Prior versions: v2.0.1 locked 2026-08-20, v2.0 locked 2026-08-20, v1.1 locked 2026-06-14, v1.0 locked 2026-05-17. Changes require both engineering seats + CEO sign-off.

**v2.1 amendment summary:** Pins the canonical scored-row inventory to `tools.active_test_registry.count_canonical_scored_rows`. A row counts only when all five predicates hold: active status, canonical runtime case, scored reporting status, execution evidenced, and target bound. `case_class != "attestation"` is not a substitute. The Auditor uses the same helper and `canonical_scored_row_count` summary field for close-over-close comparison. Accepted ruling: BEAAA-24536. Implementation: BEAAA-24975.

**v1.1 amendment summary:** Adds `merged_sha_on_canon_master_at_close` closure-time field + Auditor reachability check + Strategic-seat guarantee 5 + Operational-seat guarantee 7. Closes the merge-verification gap surfaced by the BEAAA-3228 chain (BEAAA-5284 Founder escalation). Authored by CTO (ADR-0031 on BEAAA-5288), CEO sign-off via `request_confirmation 40b788f1` 2026-06-14T17:20:50Z, Scanner Engineer retroactive co-sign tracked in BEAAA-5320 per CEO ruling on BEAAA-5318.

**v2.0 amendment summary:** Corrects all four `--is-ancestor` command sites to require `git fetch origin` before the check and to compare against `origin/master` instead of local `master`. The v1.1 formulation using bare `master` was verifying ancestry against the local ref, which silently passes when the local ref is stale and the sha has not yet been merged to canonical master HEAD — exactly the Founder-tier false-pass scenario. `origin/master` is the authoritative remote ref; the fetch makes it current at verification time. Grep evidence: `--is-ancestor` = 4 occurrences; `origin/master` command sites after this amendment = 4. Authored by CTO (BEAAA-24477), `ceo_signoff_ref: BEAAA-24459`. Landing ref: BEAAA-24477.

**v2.0.1 amendment summary:** Corrects the tamper-resistance table row to distinguish direction: changes that *reduce* protection require Founder sign-off; changes that *increase* protection require CTO co-sign only. The v2.0 row was ambiguous — "tamper-resistance posture change" without direction caused the Scanner Engineer seat to escalate all hardening work to Founders unnecessarily. Authored by CTO (BEAAA-24477), `ceo_signoff_ref: BEAAA-24459`. Stale-ancestor finding: prior exit-0 closures under v1.1 stand; no re-check sweep is required because the false-pass risk was one-directional (local master ahead of remote could never occur on the canonical workspace). Copy census: this file is the **single canonical copy**. The 15-copy census measured in BEAAA-24459 covered seat OS files and deployment snapshots; those copies are illustrative/read-only and are not authoritative. The frozen git history snapshot is excluded from the census — historical revisions are archival, not operative copies. Landing ref: BEAAA-24477.

**Source of truth:** This file. Persona text in either OS file is illustrative.

***

## Purpose

The Scanner Engineer executes probe implementations, eval-framework wrappers, CLI plumbing, customer-side harnesses, and evidence-pipeline glue daily. The CTO owns the rules these actions execute under (rubric design, scoring math, judge-model decisions, tamper-resistance posture, contract version policy). Without a clear contract, the operational seat either over-reaches (merges a rule change without CTO co-sign) or under-executes (waits for sign-off on routine throughput). This contract draws the line.

It mirrors the existing `contracts/uw-rubric-supervision-v1.md` — same shape, different domain.

## What is a "rule change" vs. "rule execution"?

| Action                                                                                         | Classification                                                                            | Requires CTO co-sign?                                        |
| ---------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| Implement a new probe inside an existing rubric\_point\_id                                     | **Rule execution**                                                                        | NO (record in ticket with N\=5 delta)                        |
| Implement a probe such that the N\=5 score impact exceeds ±1 on any golden agent               | **Rule change**                                                                           | YES                                                          |
| Add a new rubric\_point\_id to the canonical scoring sheet                                     | **Rule change**                                                                           | YES + ADR                                                    |
| Retire an existing rubric\_point\_id                                                           | **Rule change**                                                                           | YES + ADR                                                    |
| Change the weight on a rubric\_point\_id                                                       | **Rule change**                                                                           | YES (Founder if pillar weight)                               |
| Change pillar weight (GOV / ROB / BIAS / OPS)                                                  | **Rule change**                                                                           | YES + Founder sign-off                                       |
| Wrap an upstream eval framework (Inspect / PyRIT / Garak / BBQ / HolisticBias) as a BackBond Scanner probe | **Rule execution**                                                                        | NO (record in ticket with N\=5 delta + upstream version pin) |
| Bump the upstream eval framework's version                                                     | **Rule execution** if behavioral delta within ±1 on golden set; **rule change** otherwise | Conditional on N\=5 result                                   |
| Add or change a judge model                                                                    | **Rule change**                                                                           | YES                                                          |
| Change scoring math (band cuts, normalization, confidence calculation)                         | **Rule change**                                                                           | YES + ADR + Underwriter co-sign                              |
| `bin/are-scan` CLI flag addition (e.g. new --output-format)                                    | **Rule execution**                                                                        | NO                                                           |
| `bin/are-scan` CLI behavior change that affects output JSON shape                              | **Rule change**                                                                           | YES + ADR (contract version bump)                            |
| Customer onboarding harness for a new customer archetype                                       | **Rule execution**                                                                        | NO (CMO + CBDO awareness, not CTO co-sign)                   |
| Customer onboarding harness that introduces a new evidence type                                | **Rule change**                                                                           | YES                                                          |
| Smoke test for an existing operation                                                           | **Rule execution**                                                                        | NO                                                           |
| Tamper-resistance posture change (reducing protection)                                         | **Rule change**                                                                           | YES + Founder sign-off                                       |
| Tamper-resistance posture change (increasing protection)                                       | **Rule change**                                                                           | YES (CTO co-sign only)                                       |
| Refactor that doesn't touch behavior (move code, rename internal variables)                    | **Rule execution**                                                                        | NO (still requires N\=5 on golden as regression check)       |

## Required artifacts per Operational action

Every BEAAA ticket the Scanner Engineer closes carries:

* `scanner_version`
* `corpus_version`
* `probe-set-version`
* `rubric_point_id` range affected
* `N=5 calibration delta` on the golden set
* `upstream_eval_version` if the action wraps an external eval framework
* `cto_cosign_ref` — required iff the action is a rule change; pointer to the BEAAA-N ticket or ADR where CTO signed off
* `scored_case_inventory_before_after` — required iff the action adds, removes, or re-floors scored cases at any `rubric_point_id`. Record the per-point before/after counts and canonical total. Produce the canonical total with `tools.active_test_registry.count_canonical_scored_rows`, whose five required predicates are active status, canonical runtime case, scored reporting status, execution evidenced, and target bound. `case_class != "attestation"` is not an allowed substitute.
* `merged_sha_on_canon_master_at_close` — required iff the action involves a code commit (probe implementation, judge swap, scoring-math change, CLI behavior change, harness evidence-type change, refactor that touches an emission path, etc.). The cited sha MUST be reachable from canonical Scanner master HEAD at the time the ticket is closed `done`. Required on **both** rule-change and rule-execution tickets that ship code — the gate matches commit-shape, not classification-shape. The closing agent verifies reachability via `git fetch origin && git merge-base --is-ancestor <sha> origin/master` against the canonical workspace path (current pin: `/mnt/paperclipdata/dot-paperclip/instances/default/companies/59f8876e-e729-4dda-98f9-1317c2b50492/projects/are-scanner`) and quotes the verification output in the closure comment. A code-commit ticket closed without this field, or with a sha not reachable from `origin/master` HEAD, is a contract breach. The Auditor flags it next pass.

A ticket closed without these fields is a contract breach. The Auditor flags it next pass.

## What the Strategic seat (CTO) guarantees

1. **48-hour first-touch SLA on co-signs.** Co-sign asks land in a queue. Asks older than 48 hours are surfaced in the Scanner Engineer's Friday status to CEO.
2. **A clear "rule change" / "rule execution" call when escalated.** If the Scanner Engineer escalates an unclear case, the CTO decides and the call is recorded against the rubric\_point\_id family for future reference.
3. **No silent rule-changes via the strategic seat.** If the CTO wants to change a rubric point or weight, it goes through a BEAAA ticket assigned to the Scanner Engineer with the rule-change classification stated up front.
4. **Specification fidelity.** The CTO commits to rubric specs that are implementable without ambiguity. If the Scanner Engineer encounters an ambiguous spec, the CTO clarifies — they don't push interpretation down.
5. **Merge verification at co-sign-chain close.** Before PATCHing `status: done` on any rule-change ticket whose implementation involves a code commit — including downstream "merge co-sign" tickets in the same chain — the CTO runs `git fetch origin && git merge-base --is-ancestor <sha> origin/master` against the canonical Scanner workspace and quotes the output in the closure comment. "Merged via co-sign chain" is not a substitute. Failure mode reference: BEAAA-5284 Founder escalation.

## What the Operational seat (Scanner Engineer) guarantees

1. **Classify before action.** Every ticket is classified rule-change / rule-execution at intake, with the table row referenced. Self-classification plus CTO veto \= no surprise reviews.
2. **N\=5 delta on every action.** Calibration delta on the golden set runs before every merge — even for rule-execution actions. Discipline is invariant; supervision varies.
3. **Halt and escalate on band misclassification.** If you classify a ticket as rule-execution and the N\=5 delta comes back >1 on any golden, halt the merge, re-classify as rule-change, request CTO co-sign.
4. **Wrap don't fork.** Upstream eval frameworks (Inspect / PyRIT / Garak / BBQ / HolisticBias) are wrapped as probes; their logic is not copied into BackBond Scanner code. The Auditor checks for duplicate-logic drift in every release.
5. **Surface deprecated-term incidents.** BMI / ARI / "BEA" appearances in active artifacts are flagged to glossary review, not silently propagated.
6. **No commercial commitments.** No customer naming, no premium quotes, no carrier mentions in any external output. All such routes through CMO / CBDO / CTO.
7. **Merge verification before close.** Before PATCHing `status: done` on any code-commit ticket the Scanner Engineer owns the close on (rule-execution self-closes; rule-change implementation tickets), the Scanner Engineer runs `git fetch origin && git merge-base --is-ancestor <sha> origin/master` against the canonical Scanner workspace and quotes the output in the closure comment. If the sha is not an ancestor of `origin/master` HEAD, the close is invalid; resolve the merge first, then close.

## Approval gates

* **Scanner Engineer unilaterally:** rule-execution actions per the classification table.
* **CTO co-sign:** all rule-change actions; tickets where Scanner Engineer is uncertain about classification.
* **CTO + Underwriter co-sign:** rule changes that affect the contract output shape, scoring math, or band cuts.
* **CEO:** any contract change to this document; any pattern of >3 Scanner Engineer misclassifications in 14 days (Auditor flag).
* **Founders:** pillar-weight changes; tamper-resistance posture changes that reduce protection; covered-peril definitions affecting the Scanner side of the Underwriting contract.

## Audit hook

The Auditor (per `agents/auditor-os.md`) audits this contract nightly:

* Ticket metadata completeness (all required fields present?).
* Rule-change tickets that lack a `cto_cosign_ref`.
* Co-sign asks aging past 48 hours.
* Scanner Engineer self-classification accuracy (vs. CTO veto rate).
* Duplicate-logic drift between the CLI and the in-agent scanner (does the same probe family appear implemented twice?).
* Upstream eval framework version drift (probes still pinned to the version declared in their wrap-ticket?).
* Scored-case set changes closed without `scored_case_inventory_before_after` or `cto_cosign_ref`. Compare `canonical_scored_row_count`, produced by `tools.active_test_registry.count_canonical_scored_rows`, at close against the previous close. The Auditor must use the shipped helper rather than reimplementing its predicates.
* `merged_sha_on_canon_master_at_close` reachability on every code-commit ticket closed in the last 24 hours. Run `git fetch origin && git merge-base --is-ancestor <sha> origin/master` against the canonical Scanner workspace. Non-ancestor shas are flagged **Founder-tier** (Supreme-Law adjacent: rule shipped without merge to canon). Tickets missing the field on a code-commit closure are flagged at the standard tier.

## Versioning

Semver. Major \= changes to the classification table. Minor \= SLA changes or new artifact fields. Patch \= wording clarifications.

## Why this exists

The Scanner Engineer ships throughput daily. Without explicit classification, every ticket either over-burdens the CTO (asks for sign-off on every probe wrap) or under-burdens it (silently merges rule changes). This contract is the seam. It mirrors `uw-rubric-supervision-v1.md` exactly — same pattern, different domain — because the bundle's discipline is uniform across strategic/operational seat pairs.

## Artifact metadata

```yaml
owner: CEO
artifact_type: contract
status: final
updated_at: 2026-08-20
version: v2.1
supersedes: scanner-implementation-supervision-v1@v2.0.1
source_refs: [BEAAA-24975, BEAAA-24536, ADR-0070]
claim_or_decision: The canonical scored-row total and Auditor close-over-close check use the same shipped five-predicate helper. ADR-0070 published count 2,279 remains distinct from executable count 2,339.
approvals: ["F2 ruling accepted on BEAAA-24536", "implementation on BEAAA-24975"]
```
