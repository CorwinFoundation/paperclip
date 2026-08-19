# ADR-0071 — Family applicability is resolved at the dispatch boundary

    status: proposed (pending QA gate BEAAA-18614)
    date: 2026-08-19
    ticket_ref: BEAAA-24612 (child of BEAAA-18614, parent BEAAA-18610)
    owner: CTO
    supersedes_in_part: the applicability placement in PR #201 (56ecf470)
    baseline: master c76d2b92

## Context

`mcp_tool_integrity` is a conditional family: it only means anything against a
target that integrates MCP or A2A tools. That condition is declared in three
places — `FamilySpec.applicability_flag`, the family module's
`APPLICABILITY_FLAG`, and the rubric mapping's `applicability.gate_flag` with
`na_when_absent: true`.

`evals.aiuc_path_b.base.run_family` has always honored it. The production path,
`RegisteredCaseExecutor`, never consulted it. On master `c76d2b92` a real
`mcp_tool_integrity` row dispatched at a target with an empty capability
manifest returned `verdict=PASS` after a live target call. The scanner reported
a tool-integrity finding about an integration the target does not have.

That is a scoring-integrity defect, not a cosmetic one. The Underwriter cannot
price a verdict whose subject does not exist, and BEAAA-24008 ruling D.3 makes a
fabricated pass a defect whether or not anyone has read the output.

## Decision

Resolve applicability once, in `_execute_case`, ahead of every runner branch.

The gate reads `FamilySpec.applicability_flag` — the same field the eval library
uses — so the two cannot drift. No family list and no flag name is hardcoded in
the executor; adding a gate to another family is a one-line SPEC change that the
production path picks up on its own. Resolution is fail-closed, matching
`run_family`'s `architecture_flags.get(flag, False)` and the mapping's
`na_when_absent: true`: only an explicit `True` makes a gated family applicable,
so an absent, null or malformed manifest yields N/A rather than a probe run.
The N/A outcome is constructed without touching the subject or judge runner, so
the budget ledger records zero target and zero judge calls.

## Alternatives rejected

**Gate inside the lane-C branch (what PR #201 did).** It leaves every other
dispatch path ungated — including `_execute_content_candidate`, which is the
path QA originally reproduced the defect on — and makes correctness depend on
which arm of an `if`-chain a row reaches. Master has already re-homed these rows
between runners once; a per-branch gate silently loses its guarantee the next
time that happens.

**Hardcode `test_id == "mcp_tool_integrity"`.** Works today, and it is the third
independent copy of a fact already declared in three places. The next
conditional family would inherit the same defect.

**Treat a malformed manifest as INVALID rather than N/A.** Defensible, and the
tranche-1 structured path does exactly that. Rejected here because the immediate
requirement is zero fabricated verdicts and zero calls, which N/A gives in every
malformed case; the reason string still records what was seen, so an operator
can tell a malformed manifest from an honestly absent surface.

## Consequences

- A scan of a target that does not declare MCP now runs 32 lane-C rows, not 44,
  and reports the other 12 as N/A. That is a coverage *report* change, not a
  coverage loss: the 12 were never measuring anything on such a target.
- No score moves. These rows carry `score=None` on every path, and flag-off
  registry resolution is byte-identical to master.
- The evidence bound on master (BEAAA-21723) is unaffected: all 60
  `mcp_tool_integrity` evidence records show a live MCP surface in
  `target_metadata.mcp_tools_called`, so the gate would have admitted all of them.
- Editing the evaluator forces the `EVALUATOR_SHA256` self-hash re-pin and the
  canary manifest digest that transitively embeds it. Both are re-pinned, and the
  canary move is shown to be derived by diffing the manifest across trees.

## Related decisions recorded with this change

- **PR #201 (`56ecf470`) should be closed, not merged.** Master's BEAAA-23860
  lane-C runner supersedes it; rebasing #201 would reintroduce the older design.
- **PR #208's `tools/active_test_registry.py` hunks are not carried.** Master
  generalised `_scoring_active` to honour a mapping's own `binding` block, so the
  frozenset entry now changes nothing, and the `_merge_content_candidate_inventory`
  hunk cannot execute for rows that declare `bench_wrapper`. Measured: the YAML
  binding alone flips 0 → 44 scoring_active under the dev flag and leaves the
  production default untouched.
