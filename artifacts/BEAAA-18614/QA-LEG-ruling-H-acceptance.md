## QA leg of ruling H — **ACCEPTED**, with one clarification I'd like carried to the Founder

Recording the QA leg. I **accept the amended acceptance contract** as written in the description. My acceptance is **not conditional** on the clarification below — please don't hold the amendment on it.

### On the four suspensions: no objection

I went through ruling C looking for something load-bearing being lost, and I don't find one. The property I actually care about — that report-only rows never carry weight — is preserved through better routes than the suspended ones: ruling D.4 (isolation half) and D.7 (production-emission boundary) test the mechanism directly, whereas cohort totals and published-score continuity only ever detected it *indirectly, after the fact*. Suspending a downstream symptom while keeping the upstream invariant is the right trade. Item 5 staying reportable-with-tree-SHA rather than deleted is the right call too; a non-zero delta is still a signal even when it isn't a bar.

Item 3 getting stricter attention is correct and I've already acted on it — see below.

### The clarification: ruling A should not be readable as covering green→red regressions

Ruling A says no gate may fail a candidate "solely because a computed score, pillar total or advertised count differs from a previously computed value."

F5 on `6b75437f` was a test that **passed on base and failed on the candidate**: re-pinning `EVALUATOR_SHA256` invalidated a frozen digest of the tranche-1 canary manifest, which embeds that pin. The failing assertion compares a computed digest against a previously computed value — so it is *continuity-shaped*. A future producer could argue in good faith that ruling A suspends it.

I don't read it that way, and I don't think you do either. But I'd rather have it explicit than discover the ambiguity mid-gate:

> A test that passes on the base tree and fails on the candidate tree is a **regression**, not a continuity difference, regardless of whether the failing assertion happens to compare a previously computed value. Frozen digests and pinned constants are integrity checks, not score continuity.

Relatedly, ruling D enumerates what stays load-bearing but doesn't name **regression behavior**, which my contract's preamble requires ("independently test functional, logic, technical, and regression behavior"). I've been treating that preamble as untouched by the directive. Please confirm, or add it to ruling D.

If the Founder decides F5-class findings *are* suspended under Development Mode, that's their call to make — I'd just need it stated, because it changes what I can block on and I won't infer it.

### Action 2 — isolation-property proof, delivered

Built and passing against **current master, commit `6b1e9d092a0728a39577832df1eff7c4ce1f0b7a`, tree `56ed311f2d9e8e25f9e34bc5364dc21ae9ac731a`**. Item 9 asks for the tree SHA, so that is the figure to quote; every number below was measured on tree `56ed311f`.

I resolved master server-side with `git ls-remote` rather than trusting a local ref, because master moved twice during this gate (`fb2cf1ef` → `40abb5cb` → `6b1e9d09`). I then re-ran the proof from a clean detached worktree at that commit this cycle and reproduced exit 0, so what follows is a run I watched rather than a log I am quoting.

Two design choices worth stating, because they're what make it a proof rather than coverage:

**It is adapter-independent.** The isolation property belongs to the aggregation path, not to any adapter, so the proof drives the real `dispatch_registry_attempt` + `finalize` with a **stub worker whose numeric output I control**. That's why it runs on master today, where no lane-C adapter exists, and why it will run unchanged against the superseding SHA. A cooperative adapter cannot satisfy it. It also probes values no current adapter emits — `1e9`, `-1e9`, `NaN` — because "contributes zero weight" has to hold for anything an adapter *could* emit, not just the two values it happens to.

**It can fail, and I proved that.** This is the whole point of F1, so the proof sabotages itself: it disables `_validate_evidence_row` and substitutes a row class that lets executed rows carry weight, then re-runs and asserts it *detects* the leak. A proof that passes on both the real and the sabotaged build is inert, and the script exits non-zero and says so if that happens.

```
PART 1 — property on the real build
  cohorts run: 6; rows/cohort: 44
  numeric multiset all_pass : {'1.0': 44}
  numeric multiset all_fail : {'0.0': 44}
  aggregation surface       : {'score_contribution': [False], 'disposition': ['report-only'],
                               'bind_eligibility': 'eligible'}
  -> isolation property holds: True

PART 2 — sabotage self-test
  sabotaged aggregation surface : {'score_contribution': [True], ...}
  -> proof DETECTED the sabotage: True

RESULT: ISOLATION PROPERTY PROVEN, and the proof is demonstrably non-inert.
```

It also asserts the boundary actively *refuses* a weight-bearing registered-case row: both `score_contribution=True` and a non-report-only `disposition` must raise, and both do.

**What the proof covers, stated precisely.** On tree `56ed311f` the isolation property is enforced at two independent points in `api/full_scan_executor.py`. Construction hardcodes `score_contribution=False` and `disposition="report-only"` for every registered case (line 1016), and `_validate_evidence_row` raises `registered-case evidence must remain report-only` on any row that deviates (line 1075). My proof exercises both, which is why the sabotage self-test has to disable the validator *and* substitute a row class before the leak becomes visible. I also checked that no module outside `full_scan_executor.py` reads `score_contribution` for this path, so there is no downstream consumer that could reweight a row after the fact. The two hits in `evals/` are unrelated wave1a candidate dicts.

The limit of the claim: this proves report-only rows carry zero weight through the executor's aggregation path. It does not speak to how a future report renderer or underwriting export chooses to display those rows, which is a separate surface and would need its own check if one is ever built.

Artifacts: `artifacts/BEAAA-18614/qa-isolation-proof.py` and `qa-isolation-proof-run-master-6b1e9d09.log`.

This is now off the critical path. When the superseding SHA lands I run this file against it unchanged, plus the full 20-module regression differential, both halves pinned to the same tree per item 9.

### Not doing this cycle

Not re-testing `6b75437f`, and not recording `SUPERSEDED` until you relay the new SHA. Understood that the relay comes through you because engineering seats 403 here.

One note on the dead-park: you're right that `blocked` with an empty `blockedByIssueIds` was wrong, and it was mine. I'd set it that way because I read the execution contract's "blocked with a named unblock owner" as sufficient, and named the CTO in the comment body rather than as a first-class edge. Ruling I and step 11 resolve that; I'll use `todo` for a gate that is armed and awaiting a candidate rather than `blocked` without an edge.
