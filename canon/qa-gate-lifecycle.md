---
artifact_type: playbook
owner: ceo
persona_audience: founder
phase: 1
canonical_terms_only: true
focus_claim: "BackBond's independent QA gate constrains promotion only while a failed gate keeps generating work, so this lifecycle rule keeps a recorded CHANGES REQUESTED verdict awake instead of silently parked."
contracts_referenced: ["canon-promotion-v1"]
ticket_refs: ["BEAAA-23834", "BEAAA-23825", "BEAAA-23824", "BEAAA-23823", "BEAAA-23843", "BEAAA-15400"]
proof_points_cited:
  - "BEAAA-23825 comment f51b2435 (2026-08-17T04:32:35Z): eleven QA gates moved to todo — 20226, 21355, 21432, 21559, 21638, 22127, 23299, 23547, 23728, 23766, 23803"
  - "BEAAA-23824 comment a9c76da1 (2026-08-17T04:34:52Z): post-triage distribution of the 34 dead-parked issues"
  - "Paperclip issue API sweep of 4,000 issues, 2026-08-17T05:06Z: 91 blocked, of which 12 carry zero unresolved blockers"
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted:
  - "Canon promotion is an always-escalate item per decision-autonomy.md and canon-promotion-v1.md. Routed to the Founder on BEAAA-23834. Not in force before that approval lands."
  - "QA verification of the drafted rule found six additional invalid parks the BEAAA-23824 detection predicate could not see, four of them 8.7 to 18.3 days old. Section 4 records the correction. Flagged to the CEO on BEAAA-23834."
  - "BEAAA-18610 is a Phase-1 five-gate closeout item (BEAAA-21446) and has been invalidly blocked for 18.3 days. Phase-1 milestone risk, escalated to the CEO per escalation-matrix.md."
  - "artifact-metadata-schema.md has no `qa` value in the `owner` enum. This artifact was authored by the QA Engineer and is recorded as `owner: ceo` because the rule is CEO-endorsed and canon ownership sits with Founder/board. Schema gap flagged to the CEO."
  - "Internal-bound artifact (persona_audience: founder), so Brand Backbone fields are not required. scripts/verify-brand-backbone.sh was run at authoring time and exited 0 against the v1.6 pin."
---

# QA Gate Lifecycle

_Status: **proposed**. Routed to the Founder for approval on BEAAA-23834 per `canon-promotion-v1.md`. Until an approved change is merged, the current canon remains in force._

---

## 1. The rule

Verbatim from the BEAAA-23824 triage, CEO-endorsed:

> A canonical QA gate may use `blocked` after CHANGES REQUESTED only when it carries at least one unresolved first-class blocker that can generate a wake. The blocker should be the producer/remediation issue when the dependency graph remains acyclic. If adding that blocker would create a cycle because the producer is already blocked on the gate, leave the QA gate in `todo`; the recorded CHANGES REQUESTED verdict still prohibits promotion. `blocked` with zero unresolved blockers is invalid.

## 2. `todo` on a QA gate is not authorization to promote

This is the objection the rule will draw, so it is answered here rather than left to inference.

Promotion is gated by the recorded verdict, not by issue status. A QA gate that has recorded CHANGES REQUESTED for a candidate prohibits promotion of that candidate until QA records an evidence-backed `APPROVED` for an exact SHA or build. Issue status is a routing signal for who works next. It carries no approval authority and never has.

Reading `todo` as permission inverts the gate. It would mean a gate could authorize promotion by going quiet, which is the opposite of fail-closed. Where status and verdict appear to disagree, the verdict governs and the status is the thing to correct.

The practical consequence: moving a failed gate from `blocked` to `todo` weakens nothing. It restores the wake that `blocked` with no live blocker destroys, and the CHANGES REQUESTED verdict continues to hold the candidate.

## 3. The rule generalizes past QA gates

Any issue sitting at `blocked` with zero unresolved blockers is invalid, whatever seat owns it. Producer and remediation issues were dead-parked the same way in the BEAAA-23824 sweep, and five of the twelve invalid parks found on 2026-08-17 sit outside the QA seat.

## 4. The park-time predicate is not sufficient on its own

QA verified the drafted rule against live board state on 2026-08-17 at 05:06Z. The rule classifies these cases correctly, because it already says *unresolved*. What the BEAAA-23824 triage did not carry is a re-evaluation duty or a detection predicate that can find them.

A gate can satisfy the rule at the moment it parks, then silently violate it later. The gate adds a real remediation blocker, the blocker is resolved or cancelled, the edge remains in place, and the gate stays `blocked` behind a dependency that no longer exists. Nothing fires.

An API sweep of 4,000 issues found 91 at `blocked`, of which 12 carried zero unresolved blockers. Ten of those twelve carried at least one blocker edge that had gone terminal, and only 2 had a genuinely empty `blockedBy`. The longest-standing and most recent cases:

| Issue | Blocker edge | Edge status | Time invalidly blocked |
| --- | --- | --- | --- |
| BEAAA-18610 | 18613, 18614 | cancelled | 18.3 days |
| BEAAA-19988 | 20206 | cancelled | 15.5 days |
| BEAAA-21040 | 21041 | cancelled | 9.7 days |
| BEAAA-20704 | 21391 | done | 8.7 days |
| BEAAA-21949 | 23831 | done | under 1 hour |
| BEAAA-21432 | 21427 | done | under 1 hour |

The four multi-week cases establish that resolving or cancelling the last blocker does not wake the blocked issue. BEAAA-18610 is a Phase-1 five-gate closeout item under BEAAA-21446 and has been unreachable for 18.3 days behind two cancelled blockers.

Three additions follow.

**Re-evaluation is the responsibility of whoever closes the last blocker.** Closing or cancelling an issue that is the final unresolved blocker on another issue requires returning the blocked issue to `todo` in the same action. Resolving a dependency is not complete while the thing it was blocking is still parked behind it.

**The owning seat re-checks on wake.** An assignee waking on any issue at `blocked` confirms it still carries an unresolved blocker. If it does not, the issue returns to `todo` before other work begins.

**The detection predicate is zero unresolved blockers, not an empty array.** The BEAAA-23824 sweep tested for `blocked` with an empty `blockedBy`. Against the 2026-08-17 board that test sees 2 of the 12 invalid parks and misses 10, including all four multi-week cases. Any recurring audit reads each blocker edge and counts those whose status is neither `done` nor `cancelled`.

## 5. Genuine external dependencies

Where a real dependency exists but no internal issue can represent it, the correct posture is a typed interaction carrying `continuationPolicy: wake_assignee` together with `in_review`. Free-text `blocked` is not a substitute, because the text generates no event.

The CEO used this route on BEAAA-21718, BEAAA-23221, BEAAA-23562 and BEAAA-23698. All four were confirmed at `in_review` with zero blocker edges on 2026-08-17.

This supersedes the escape hatch in the copies described in section 7, which permitted `blocked` on the strength of a comment naming an unblock owner and action. A comment wakes nobody. The gates dead-parked in the BEAAA-23824 sweep had recorded exactly such a comment as part of their CHANGES REQUESTED verdict and still went silent.

## 6. Internally ownable dependencies

Where the dependency is real and can be owned inside the company, create the first-class blocker. The CEO did this on BEAAA-21949 via BEAAA-23831 after `todo` alone proved unstable, the owning seat having re-parked it within the hour because the underlying requirement was unsatisfiable.

BEAAA-23831 has since closed while BEAAA-21949 remains `blocked` behind it, which is the section 4 failure and the reason the re-evaluation duty is stated as a rule rather than left to practice.

Where the producer is already blocked on the gate, adding the reverse edge would create a cycle. Leave the gate in `todo` and record the reason. The alternative used successfully during the sweep is a cycle-breaking child assigned to the CTO with an empty `blockedBy`, which the gate can then block on without a cycle. BEAAA-21559 is the one gate of the eleven still holding a live unresolved blocker by this route.

## 7. Reconciliation of existing copies

Two divergent copies of a file named `qa-gate-lifecycle.md` are distributed to agent instruction directories. Neither is source-controlled in this repository. On approval, this file becomes canonical and both are replaced from it.

| Variant SHA-256 (first 8) | Seats carrying it | Dead-park guard |
| --- | --- | --- |
| `221f3b2c` | 5 | Procedure step 9, permitting the comment escape hatch section 5 removes |
| `ad22ba27` | 1 | None. Its `blocked` entry says only to identify the dependency |

Eighteen of twenty-four agent instruction directories carry no copy at all. A rule distributed to a quarter of the seats in two disagreeing versions is not in force in any meaningful sense, and this is the second thing the Founder is being asked to fix.

The `221f3b2c` step 10, added by the CEO on BEAAA-23843 after BEAAA-23823 cleared four gates that had failed this way, is preserved:

> When an engineering seat is HTTP 403 on its own canonical QA child (`Issue is outside this actor's authorization boundary`), the sanctioned handback is: comment the new candidate SHA and evidence on the implementation parent, then escalate to the CEO seat, which relays onto the gate and resets status. Do not open a sibling gate to route around the 403.

## 8. Status use

- `todo`: the canonical gate awaits a candidate, or holds a CHANGES REQUESTED verdict with no live blocker. Promotion remains prohibited by the verdict.
- `in_progress` or `in_review`: QA is actively verifying. `in_review` also covers a typed interaction on `wake_assignee` per section 5.
- `blocked`: valid only while at least one blocker edge is unresolved. On the last edge going terminal, the issue returns to `todo` per section 4.
- `done`: the exact candidate received an evidence-backed `APPROVED`.
- `cancelled`: duplicate or superseded gate only, carrying a pointer to the canonical gate.

## 9. Affected roles and contracts

Identified per `canon-promotion-v1.md`.

| Affected | Change |
| --- | --- |
| QA Engineer | Loses `blocked` as a resting state after CHANGES REQUESTED unless a live blocker exists. Gains the section 4 wake-time re-check. |
| CTO and engineering seats | Closing a final blocker now carries the duty to return the blocked issue to `todo`. |
| CEO | Owns the recurring audit on the corrected section 4 predicate, and the control-plane disposition when a cycle blocks the normal route. |
| Auditor | Detection predicate changes from empty `blockedBy` to zero unresolved blocker edges. |
| All seats | Section 3 applies the rule outside QA. |
| `canon-promotion-v1` | Referenced as the routing contract. Unchanged. |
| BEAAA-15400 | Amended, not weakened. Independent evidence-backed QA `APPROVED` for an exact SHA or build remains required before any shared or production promotion, and Founder sign-off remains separately required where applicable. |

No scoring, pillar-weight, rubric, corpus, brand-canon, or API-contract surface changes.

## 10. Rollback

Revert this file and restore the two instruction-directory variants from `221f3b2c` and `ad22ba27`. The change is procedural and carries no dependent logic or build surface, so rollback is mechanical. Trigger for rollback: the platform gains a native wake on blocker resolution, at which point section 4 should be re-derived against the new behaviour rather than left in place.
