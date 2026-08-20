# Scanner Implementation Supervision Contract (v2.1)

**Parties:** Scanner Engineer (operational seat) ↔ CTO (strategic seat); Underwriter (Head of UW, co-counterparty on `contracts/are-scanner-api-v1.md`); Auditor (audits the supervision log).

**Status:** Locked at v2.1 on 2026-08-20. Prior versions: v2.0.1 locked 2026-08-20, v2.0 locked 2026-08-19, v1.1 locked 2026-06-14, v1.0 locked 2026-05-17. Changes require both engineering seats + CEO sign-off.

**v2.1 amendment summary:** Pins the canonical scored-row inventory to the shipped `tools.active_test_registry.count_canonical_scored_rows` helper. A row counts only when all five predicates hold: `status == "active"`; `canonical_runtime_case is True`; `reporting_status == "scored"`; `execution_evidenced is True`; and `target_bound is True`. The convention deliberately does not substitute `case_class != "attestation"`. The Auditor's close-over-close comparison uses this same helper and the `canonical_scored_row_count` summary field. Minor bump because this adds a shared measurement convention without changing the classification table. Accepted ruling: BEAAA-24536; implementation: BEAAA-24975.

**v2.0.1 amendment summary:** (a) Splits the tamper-resistance classification-table row by direction: reductions require Founder sign-off; strengthenings require CTO co-sign with Founder notification. (b) Adds the direction-evidence note beneath the table, including the (a)/(b)/(c) enumeration the CTO must provide at co-sign. (c) Corrects all four `--is-ancestor` command sites to require `git fetch origin master && git rev-parse origin/master` before the check and to compare against `origin/master` instead of local `master`; adds the `&&`-chain rationale. (d) Tightens the Founders approval-gate bullet to reference the direction note. (e) Updates Versioning to clarify that a direction-split of an existing row is patch-level when the Classification column is unchanged. Patch-level per the updated Versioning definition: the Classification column is unchanged for both resulting rows, and the sign-off column is only made explicit. `ceo_signoff_ref: BEAAA-24459`. Landing ref: BEAAA-24477. Stale-ancestor finding: prior exit-0 closures under v1.1/v2.0 stand; no re-check sweep required (see standing ruling below).

**v2.0 amendment summary:** Adds a classification-table row for changes to the scored-case set at an existing `rubric_point_id`, narrows the "new probe inside an existing rubric_point_id" row to actions that leave the scored-case set unchanged, adds the `scored_case_inventory_before_after` closure field, and adds the matching Auditor check. Major bump because the classification table changed. Closes the ambiguity surfaced on BEAAA-24490: BEAAA-23857 (182 to 214 canonical scored rows at P1.2, P1.6, P2.5) ran as rule execution while BEAAA-24210 raised the same five case floors as a rule change under CTO co-sign. Proposed by CTO on BEAAA-24522, CEO sign-off recorded on BEAAA-24522 2026-08-19, Scanner Engineer retroactive co-sign tracked in the child issue named on that ticket.

**v1.1 amendment summary:** Adds `merged_sha_on_canon_master_at_close` closure-time field + Auditor reachability check + Strategic-seat guarantee 5 + Operational-seat guarantee 7. Closes the merge-verification gap surfaced by the BEAAA-3228 chain (BEAAA-5284 Founder escalation). Authored by CTO (ADR-0031 on BEAAA-5288), CEO sign-off via `request_confirmation 40b788f1` 2026-06-14T17:20:50Z, Scanner Engineer retroactive co-sign tracked in BEAAA-5320 per CEO ruling on BEAAA-5318.

**Source of truth:** This file. Persona text in either OS file is illustrative.

***

## Purpose

The Scanner Engineer executes probe implementations, eval-framework wrappers, CLI plumbing, customer-side harnesses, and evidence-pipeline glue daily. The CTO owns the rules these actions execute under (rubric design, scoring math, judge-model decisions, tamper-resistance posture, contract version policy). Without a clear contract, the operational seat either over-reaches (merges a rule change without CTO co-sign) or under-executes (waits for sign-off on routine throughput). This contract draws the line.

It mirrors the existing `contracts/uw-rubric-supervision-v1.md` — same shape, different domain.

## What is a "rule change" vs. "rule execution"?

| Action                                                                                         | Classification                                                                            | Requires CTO co-sign?                                        |
| ---------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| Implement a new probe inside an existing rubric\_point\_id that leaves the scored-case set at that point unchanged (reported-not-scored probe, or a replacement at constant scored-case count) | **Rule execution**                                                | NO (record in ticket with N\=5 delta + the unchanged scored-case count) |
| Add, remove or re-floor scored cases at an existing rubric\_point\_id (changes the live pass-rate denominator) | **Rule change**                                                          | YES (record the before-and-after scored-case inventory at every affected point) |
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
| Tamper-resistance posture **reduction**                                                        | **Rule change**                                                                           | YES + Founder sign-off                                       |
| Tamper-resistance posture **strengthening**                                                    | **Rule change**                                                                           | YES (CTO co-sign; Founder notified, non-blocking)            |
| Refactor that doesn't touch behavior (move code, rename internal variables)                    | **Rule execution**                                                                        | NO (still requires N\=5 on golden as regression check)       |

> **Direction of a tamper-resistance change is an evidenced claim, not an assertion.** In the co-sign comment the CTO enumerates: (a) every caller-controlled or otherwise external input the change removes from the attested path; (b) every input it adds; (c) every new failure path it introduces, and where each one fails. The change is **strengthening** only if (b) is empty and every path in (c) fails closed. If it both removes and adds inputs, or if either engineering seat cannot attest that no path is weakened, it is a **reduction** and takes the Founder gate. Direction is judged over the whole change, not the headline path: hardening one path while relaxing another is a reduction. Where direction is contested, the reduction row applies until the Founder rules.
>
> **"Founder notified, non-blocking"** means the CTO posts the (a)/(b)/(c) enumeration on the implementing ticket at co-sign time, and the CEO carries it into the next daily board report. Notification is due at co-sign, not at promotion, so the Founder's standing veto is exercisable while it still costs nothing to exercise.

**Why the scored-case row exists.** The `N=5 delta` clause that makes the rule-execution rows safe cannot see a scored-case set change. The golden calibration path (`harness/golden_set/calibration_delta.py`) re-scores locked per-rubric-point `raw` values from `artifacts/beaaa-387/*/eval_outputs.json` against the current rubric; no fixture on either N=5 roster carries a `pass_rate` key, so a case-count denominator never reaches `rubric.scoring.score`. A ticket can move the live pass-rate denominator at a scored point and still report a golden delta of 0. Read at `origin/master` `5410126`, re-verified by CEO on 2026-08-19. Treat "N=5 delta is 0" as evidence of nothing for this class of action.

## Required artifacts per Operational action

Every BEAAA ticket the Scanner Engineer closes carries:

* `scanner_version`
* `corpus_version`
* `probe-set-version`
* `rubric_point_id` range affected
* `N=5 calibration delta` on the golden set
* `upstream_eval_version` if the action wraps an external eval framework
* `cto_cosign_ref` — required iff the action is a rule change; pointer to the BEAAA-N ticket or ADR where CTO signed off
* `scored_case_inventory_before_after` — required iff the action adds, removes or re-floors scored cases at any `rubric_point_id`. Per-point scored-case count before and after, plus the canonical total, for every affected point. This is the field the golden N\=5 delta cannot substitute for.
  The canonical total MUST be produced by `tools.active_test_registry.count_canonical_scored_rows` (also exposed as `canonical_scored_row_count` by `summarize`). Its convention is exactly: active status, canonical runtime case, scored reporting status, execution evidenced, and target bound. All five predicates are required; `case_class != "attestation"` is not an allowed substitute.
* `merged_sha_on_canon_master_at_close` — required iff the action involves a code commit (probe implementation, judge swap, scoring-math change, CLI behavior change, harness evidence-type change, refactor that touches an emission path, etc.). The cited sha MUST be reachable from canonical Scanner master HEAD at the time the ticket is closed `done`. Required on **both** rule-change and rule-execution tickets that ship code — the gate matches commit-shape, not classification-shape. The closing agent verifies reachability by running, at the pinned canonical workspace, `git fetch origin master && git rev-parse origin/master && git merge-base --is-ancestor <sha> origin/master`, and quotes every command with its output and exit code. The `&&` chain is required rather than stylistic: a fetch that fails, for example on an expired credential, leaves a stale `origin/master` that still resolves cleanly, so an unchained `merge-base` would return a confident answer about a ref that was never refreshed. A local `master` ref must not be used; it is not guaranteed to track canon. Canonical workspace pin: `/mnt/paperclipdata/dot-paperclip/instances/default/companies/59f8876e-e729-4dda-98f9-1317c2b50492/projects/are-scanner`. A code-commit ticket closed without this field, or with a sha not reachable from `origin/master` HEAD, is a contract breach. The Auditor flags it next pass.

A ticket closed without these fields is a contract breach. The Auditor flags it next pass.

## What the Strategic seat (CTO) guarantees

1. **48-hour first-touch SLA on co-signs.** Co-sign asks land in a queue. Asks older than 48 hours are surfaced in the Scanner Engineer's Friday status to CEO.
2. **A clear "rule change" / "rule execution" call when escalated.** If the Scanner Engineer escalates an unclear case, the CTO decides and the call is recorded against the rubric\_point\_id family for future reference.
3. **No silent rule-changes via the strategic seat.** If the CTO wants to change a rubric point or weight, it goes through a BEAAA ticket assigned to the Scanner Engineer with the rule-change classification stated up front.
4. **Specification fidelity.** The CTO commits to rubric specs that are implementable without ambiguity. If the Scanner Engineer encounters an ambiguous spec, the CTO clarifies — they don't push interpretation down.
5. **Merge verification at co-sign-chain close.** Before PATCHing `status: done` on any rule-change ticket whose implementation involves a code commit — including downstream "merge co-sign" tickets in the same chain — the CTO runs `git fetch origin master && git rev-parse origin/master && git merge-base --is-ancestor <sha> origin/master` against the canonical Scanner workspace and quotes every command with its output and exit code in the closure comment. "Merged via co-sign chain" is not a substitute. Failure mode reference: BEAAA-5284 Founder escalation.

## What the Operational seat (Scanner Engineer) guarantees

1. **Classify before action.** Every ticket is classified rule-change / rule-execution at intake, with the table row referenced. Self-classification plus CTO veto \= no surprise reviews.
2. **N\=5 delta on every action.** Calibration delta on the golden set runs before every merge — even for rule-execution actions. Discipline is invariant; supervision varies.
3. **Halt and escalate on band misclassification.** If you classify a ticket as rule-execution and the N\=5 delta comes back >1 on any golden, halt the merge, re-classify as rule-change, request CTO co-sign.
4. **Wrap don't fork.** Upstream eval frameworks (Inspect / PyRIT / Garak / BBQ / HolisticBias) are wrapped as probes; their logic is not copied into BackBond Scanner code. The Auditor checks for duplicate-logic drift in every release.
5. **Surface deprecated-term incidents.** BMI / ARI / "BEA" appearances in active artifacts are flagged to glossary review, not silently propagated.
6. **No commercial commitments.** No customer naming, no premium quotes, no carrier mentions in any external output. All such routes through CMO / CBDO / CTO.
7. **Merge verification before close.** Before PATCHing `status: done` on any code-commit ticket the Scanner Engineer owns the close on (rule-execution self-closes; rule-change implementation tickets), the Scanner Engineer runs `git fetch origin master && git rev-parse origin/master && git merge-base --is-ancestor <sha> origin/master` against the canonical Scanner workspace and quotes every command with its output and exit code in the closure comment. If the sha is not an ancestor of `origin/master` HEAD, the close is invalid; resolve the merge first, then close.

## Approval gates

* **Scanner Engineer unilaterally:** rule-execution actions per the classification table.
* **CTO co-sign:** all rule-change actions; tickets where Scanner Engineer is uncertain about classification.
* **CTO + Underwriter co-sign:** rule changes that affect the contract output shape, scoring math, or band cuts.
* **CEO:** any contract change to this document; any pattern of >3 Scanner Engineer misclassifications in 14 days (Auditor flag).
* **Founders:** pillar-weight changes; tamper-resistance **reductions** (direction determined per the note under the classification table); covered-peril definitions affecting the Scanner side of the Underwriting contract.

## Audit hook

The Auditor (per `agents/auditor-os.md`) audits this contract nightly:

* Ticket metadata completeness (all required fields present?).
* Rule-change tickets that lack a `cto_cosign_ref`.
* Co-sign asks aging past 48 hours.
* Scanner Engineer self-classification accuracy (vs. CTO veto rate).
* Duplicate-logic drift between the CLI and the in-agent scanner (does the same probe family appear implemented twice?).
* Upstream eval framework version drift (probes still pinned to the version declared in their wrap-ticket?).
* Scored-case set changes closed without `scored_case_inventory_before_after` or without a `cto_cosign_ref`. Compare `canonical_scored_row_count`, produced by `tools.active_test_registry.count_canonical_scored_rows`, at close against the value at the previous close; an unexplained move is flagged at the standard tier. The Auditor must use the shipped helper rather than reimplementing or approximating its five predicates.
* `merged_sha_on_canon_master_at_close` reachability on every code-commit ticket closed in the last 24 hours. Run `git fetch origin master && git rev-parse origin/master && git merge-base --is-ancestor <sha> origin/master` against the canonical Scanner workspace. Non-ancestor shas are flagged **Founder-tier** (Supreme-Law adjacent: rule shipped without merge to canon). Tickets missing the field on a code-commit closure are flagged at the standard tier.

## Versioning

Semver. **Major** \= a change to the *Classification* column for any listed action, or the addition or removal of a covered action. **Minor** \= SLA changes or new artifact fields. **Patch** \= wording clarifications, including splitting an existing row into direction-specific or condition-specific rows, provided every resulting row keeps the same Classification and the sign-off column is only made explicit.

## Standing rulings on this contract

* **v2.0 binds prospectively.** The scored-case row applies to tickets opened or still open at 2026-08-19. It does not reopen closed tickets. BEAAA-23857 was corrected to rule change on the record under the Head of UW ruling on BEAAA-24490 (comment `95977f63`); no misclassification is charged to the Scanner Engineer seat for it, and the Auditor's ">3 misclassifications in 14 days" trigger does not count it. The cited row said what it said.
* **Branching-policy text is not in force.** Between 2026-06-14 and 2026-08-19 the CTO seat's copy of this file carried three extra sentences referencing `engineering/scanner/branching-policy.md` that the three Scanner Engineer copies did not, with all four labelled v1.1. That text never carried the CEO sign-off this contract's Escalation section requires, and the file it cites does not exist at `origin/master` `5410126`. It is excluded from v2.0. The CTO may land the policy file and re-propose the text as a minor amendment.
* **v2.0.1 stale-ancestor finding.** The bare-`master` defect in v2.0 and earlier under-approximates reachability, never over-approximates: a strict ancestor check against a behind-but-not-divergent local `master` can only produce false negatives (blocking a valid close) and cannot produce false positives (passing an un-merged sha). Exit-0 results quoted against local `master` at the canonical pin imply exit 0 against `origin/master`. Prior closures verified against local `master` stand; no re-check sweep is required. The false-pass risk (local `master` ahead of `origin/master`) is a separate concern; a closing agent who measures local `master` ahead of `origin/master` must stop and escalate rather than apply this ruling.
* **Copy integrity.** All seat copies of this file are byte-identical at lock. Any seat reading a copy whose version header does not match the version recorded on the CEO's lock ticket should stop and escalate rather than apply the local text.

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
source_refs: [BEAAA-24975, BEAAA-24536, BEAAA-24477, BEAAA-24459, BEAAA-24522, BEAAA-24490, BEAAA-23857, BEAAA-24210, "commit:5410126", "harness/golden_set/calibration_delta.py", "artifacts/beaaa-387/B1/eval_outputs.json"]
claim_or_decision: The canonical scored-row total uses the shipped five-predicate helper and the Auditor uses the same helper for close-over-close checks. Tamper-resistance direction and origin/master reachability language remain as locked in v2.0.1.
ceo_signoff_ref: BEAAA-24459
approvals: ["F2 ruling accepted on BEAAA-24536", "implementation on BEAAA-24975", "CTO proposal on BEAAA-24477", "CEO sign-off on BEAAA-24459", "Scanner Engineer co-sign pending — tracked on BEAAA-24873 child"]
```
