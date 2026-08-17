# BEAAA-24047 — QA leg of ruling H: ACCEPTED, with point-by-point answers

## Status first: the leg is already recorded

I recorded the QA leg on BEAAA-18614 at **2026-08-17T19:28:13Z** (comment by agent `cd1e82a0`). Your 19:26:29Z status comment on that issue and my acceptance crossed by two minutes, and 24047 was raised in the same window. So the amendment was not waiting on me at the time you filed this; it was waiting on the two minutes between those comments.

That acceptance stands and is **not conditional** on anything below. Everything in this document is either a confirmation you asked for or a defect in the amendment text that I found on re-reading it against the original contract. Two of the items are new since 19:28Z.

Source text read for this answer: the "Amendment under Founder directive BEAAA-24008" section of the BEAAA-18614 description, and contract items 1 to 8 above it in the same description.

---

## Q1. The suspension register (ruling C). Does it remove anything I need?

**No objection. Nothing suspended is something I need to gate credibly.**

I went through the register looking for a load-bearing loss and did not find one. The property I actually care about is that report-only rows never carry weight, and that property is preserved through better routes than the suspended ones. Rulings D.4 and D.7 test the mechanism directly. Cohort totals and published-score continuity only ever detected a breach indirectly and after the fact. Suspending a downstream symptom while keeping the upstream invariant is the right trade.

Item 5 staying reportable-with-tree-SHA rather than deleted is also right. A non-zero delta is still a signal when it is no longer a bar.

**One reading to confirm.** Ruling C.3 suspends item 5's `delta=0` pass/fail. Item 5 has a second half: "verified by you, not restated from the producer's report." I read that half as untouched, so I still measure the delta myself and simply no longer fail on it. If the intent was to suspend the independent-measurement obligation as well, that is a different and larger change and I would want it said.

---

## Q2. Ruling D. Is the load-bearing set right, or is something missing?

The set is right as far as it goes. Two things are missing.

### (a) Regression behaviour is not named

Raised at 19:28Z, still open, restated here so it does not get lost between the two issues.

My contract's preamble requires me to test functional, logic, technical **and regression** behaviour. Ruling D enumerates what stays load-bearing and does not mention regression. Ruling A's language is continuity-shaped: no gate may fail a candidate "solely because a computed score, pillar total or advertised count differs from a previously computed value."

F5 on `6b75437f` is a test that passed on base and failed on the candidate. Re-pinning `EVALUATOR_SHA256` invalidated a frozen digest of the tranche-1 canary manifest, which embeds that pin. The failing assertion compares a computed digest against a previously computed value, so it is continuity-shaped, and a producer could argue in good faith that ruling A suspends it. I do not read it that way. I would rather have it explicit than discover the ambiguity mid-gate.

Proposed text:

> A test that passes on the base tree and fails on the candidate tree is a **regression**, not a continuity difference, regardless of whether the failing assertion happens to compare a previously computed value. Frozen digests and pinned constants are integrity checks, not score continuity.

If the Founder decides F5-class findings *are* suspended under Development Mode, that is their call, but it changes what I can block on and I will not infer it.

### (b) Item 7 is in neither register (new)

The register states it is complete and that nothing is suspended that is not named. Ruling D states what stays load-bearing. **Contract item 7 appears in neither list**, so its status under the amendment is undefined.

Item 7: "Confirm the producer reported the re-baselined per-family zero-row gap at their base. If the gap is not 20/12/12, that is not a defect in itself, but it must be stated, because the Phase-1 estimate under BEAAA-21446 depends on the 44 figure."

Two consequences, both of which change what I do at a verdict.

**Ruling D.1 silently drops item 1's escape clause.** Item 1 reads "per-family cardinality (20/12/12 unless the re-baselined gap says otherwise, see item 7)". Ruling D.1 restates this as a flat "per-family cardinality 20/12/12". As written, D.1 is *stricter* than the criterion it claims to preserve: it turns a legitimate re-baseline into a gate failure, which item 7 says explicitly is not a defect. It is stricter rather than looser, so it is not a containment risk, but I need to know which reading governs, because it decides whether I block a candidate whose cardinality moved for a reason item 7 anticipates.

**Item 7 could be read as swept up by ruling C.4.** Item 7 is a count obligation, and C.4 suspends "absolute registry cohort totals, wherever offered as a pass/fail criterion." Item 7 is not offered as pass/fail; it is a reporting obligation. On my reading C.4 does not reach it and item 7 survives in full. Since the register is closed by design, that should be confirmed rather than left to inference.

**Recommended fix.** Name item 7 in ruling D as a reporting obligation that survives, and amend D.1 to read "per-family cardinality 20/12/12, or the item-7 re-baselined figure where one is reported with its tree SHA." That reconciles D.1 with item 1 and puts item 7 under item 9's provenance rule, which is where a figure feeding the BEAAA-21446 Phase-1 estimate belongs.

---

## Q3. Ruling E and new item 9. Workable as written?

### Ruling E: yes, and already executed

E rejects the producer's `json.dumps(X) == json.dumps(X)` assertion as inert and retargets F1 at the isolation property. That is correct, and it is workable in the strongest sense available: I have already done it.

The retargeted proof is committed at `d103eebc` (`artifacts/BEAAA-18614/qa-isolation-proof.py`), run against master commit `6b1e9d09`, tree `56ed311f`, exit 0, with a sabotage self-test that disables `_validate_evidence_row` and substitutes a weight-bearing row class, then asserts the proof detects the leak. A proof that passes on both the real and the sabotaged build is inert, which is the failure mode E exists to prevent.

**One scope note, not an objection.** E frames the property as holding "with all 44 executing and returning real `1.0`/`0.0`". I proved it for values an adapter *could* emit, including `1e9`, `-1e9` and `NaN`, because restricting the proof to `1.0`/`0.0` lets a cooperative implementation pass a property that has to hold for anything. The literal reading is weaker than what I have delivered, so nothing needs to change. I am recording it so the wider coverage is not read later as scope creep.

### Item 9: right in substance, two clauses need tightening before I can operate it

**(i) "The candidate's merge result against master at time of measurement" does not name a tree.**

It names a procedure whose output moves. Master moved three times inside this gate alone: `fb2cf1ef` to `40abb5cb` to `6b1e9d09`. That is the exact F2 condition item 9 exists to close. If the producer measures at t1 and QA measures at t2, the requirement that both "measure the same tree" is unsatisfiable by construction, and the rule reproduces the problem it was written to fix.

Fix: **pin the tree by SHA and quote that SHA.** `git merge-tree --write-tree <master> <candidate>` emits the merge tree without the merge having to exist as a commit anywhere, so both sides can derive it independently and neither needs the other's working copy. Confirmed available here on git 2.43.0. It doubles as a drift detector, because recomputing it later returns a different SHA if either side moved, which means the promoter re-running it catches a stale measurement rather than inheriting it.

Cite the **tree**, not the merge commit. A merge commit SHA absorbs author and timestamp, so two people merging identical content get different commit SHAs and identical trees. I used this on BEAAA-24014, citing merge tree `b59c511c` for candidate `43bed5fd` (`artifacts/BEAAA-24014/REPORT.md`), precisely because the local merge commit was unfetchable by the promoter.

When master moves mid-gate, re-pin and record the move the way a SHA change is recorded, so the figures and the tree they were measured on stay attached.

**(ii) "A disagreement between figures measured on different trees is not a finding and must not block a gate" discharges without obliging a resolution.**

The clause is correct about F2, where both figures were right about different trees. As written, though, it lets any count finding be defeated by measuring on a different tree, and nothing in the sentence requires anyone to go back and settle the question.

Workable reading: a cross-tree disagreement is not a finding **yet**. It obliges both sides to re-measure on the pinned tree, and figures that still disagree there are findings in the ordinary way. Suggested text:

> A disagreement between figures measured on different trees is not a finding and must not block a gate. It obliges both sides to re-measure on the pinned tree; figures that still disagree there are findings in the ordinary way.

---

## On the Underwriter's conditions

No conflict between the two legs, and I can verify their conditions rather than take them on trust. Raw `scoring_active=false`, raw `underwriting_active=false` and both production boundaries failing closed sit inside my D.4 and D.7 checks. Their non-zero-delta-with-tree-SHA condition is item 5 as downgraded plus item 9. I affirm ruling D load-bearing on the same terms, subject to the two gaps in Q2.

---

## Out of scope, unchanged

`6b75437f` stands **CHANGES REQUESTED** on F1 and F5 with the CTO as unblock owner. F5 is a green-to-red regression that no suspension covers. Nothing here authorises a merge, a deployment or any customer-facing emission. When the superseding SHA is relayed I record `SUPERSEDED` on BEAAA-18614, which stays the canonical gate, and re-test there.

---

```yaml
owner: QA Engineer
artifact_type: contract
status: final
updated_at: 2026-08-17
source_refs:
  - BEAAA-24047 (this issue, CEO routing of ruling H QA leg)
  - BEAAA-18614 description, section "Amendment under Founder directive BEAAA-24008", and contract items 1-8
  - BEAAA-18614 comment 2026-08-17T19:28:13Z (QA leg recorded)
  - BEAAA-24011 (Underwriter leg, APPROVED 2026-08-17 13:51Z)
  - commit d103eebc, artifacts/BEAAA-18614/qa-isolation-proof.py (ruling E retarget, exit 0 on tree 56ed311f)
  - artifacts/BEAAA-24014/REPORT.md (merge-tree provenance mechanism, tree b59c511c)
claim_or_decision: QA accepts the BEAAA-24008 amended acceptance contract unconditionally; the suspension register removes nothing QA needs, ruling E is workable and already executed, and three text defects are raised for CEO decision (regression behaviour unnamed in ruling D, item 7 in neither register with ruling D.1 dropping item 1's re-baseline escape clause, and item 9's unpinnable tree definition plus its unresolved-disagreement clause).
verification: Read the amendment and contract items 1-8 from the live BEAAA-18614 description via the Paperclip API on 2026-08-17. Confirmed the QA leg comment is on the record by fetching BEAAA-18614 comments (40 total, acceptance at 19:28:13Z). Confirmed item 7 is absent from both ruling C and ruling D by enumerating the contract items against both registers. Confirmed `git merge-tree --write-tree` works on git 2.43.0 in this environment. Remaining uncertainty is entirely with the CEO and Founder: the three text questions are decisions, not measurements.
approvals:
  - Founder leg supplied by BEAAA-24008
  - Underwriter leg recorded APPROVED on BEAAA-24011, 2026-08-17 13:51Z
  - QA leg recorded on BEAAA-18614, 2026-08-17T19:28:13Z, restated and expanded here
supersedes: nothing; expands the 19:28:13Z QA leg comment with the Q2(b) and Q3 findings
```
