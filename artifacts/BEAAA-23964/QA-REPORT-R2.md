# BEAAA-23964 R2 — QA gate report

**Verdict: CHANGES REQUESTED.** Not an approval.

- Canonical gate: BEAAA-23964 (reused; no sibling gate opened).
- Implementation parent: BEAAA-23953.
- `SUPERSEDED: f589c87b6a318dc3d884542e07c29cd0a170c4ae -> 4e68daac75247660c59f305dbec929cf12e16a6d`
- Candidate under test: `4e68daac75247660c59f305dbec929cf12e16a6d` (code `017be5ef`), branch
  `agent/beaaa-23953-focus-claim-escape`, confirmed remote tip.
- Declared base: `40abb5cb821718641f3322b0eb656c50f462e46b`.
- **Current master at test time: `b434400107829333173e08180280cf09cd366b1d`.** The declared base is one
  merge stale, and the intervening commit touches the same two files.

The fix itself is correct and the R1 blocker is closed. Both blockers below are integration
defects against current master, not defects in the candidate on its stated base. Neither is
fixable by me — routed to the CTO / Scanner Engineer #2.

---

## All seven acceptance criteria PASS on the candidate's own base

| AC | Result | Evidence |
| --- | --- | --- |
| 1 — 37 tests (34 R1, 29 base) | PASS | candidate `37 passed`; base `40abb5cb` `29 passed` |
| 2 — named importers still pass | PASS | `18 passed`; all three importers together `55 passed` |
| 3 — rejection + code point named | PASS | 32/32 C1 rejected, code point named in all 32; also `"` `\` LF TAB CR DEL VT FF NUL U+2028 U+2029 |
| 4 — round-trip for accepted claim | PASS | byte-identical through the real renderer + `yaml.safe_load` |
| 5 — mutant gives 7 failures | PASS | exactly `7 failed, 30 passed`, exactly the 7 named |
| 6 — grep returns no hits | PASS **on the candidate** | `grep -rn artifact-metadata-schema scripts/ tests/` → exit 1. **Fails on the merge result — see F-B.** |
| 7 — Supreme Law Layer 2, ±0 | PASS | byte-identical from two different trees; SHAs match the producer's claim exactly |

### R1 blocker is closed, and the diagnosis was the right one

Replaying the R1 sweep shape against R2: `rejected=32, accepted=0, message_names_code_point=32`.
R1 was `rejected=0, clean=0, unparseable=31, silently_mutated=1`.

The property-shaped check is genuinely stronger than a widened range, and I verified that claim
rather than accepting it. Two probes beyond the producer's 261-point sweep:

**P1 — completeness over the whole domain.** Using PyYAML's own
`yaml.reader.Reader.NON_PRINTABLE` as the oracle, over all 1,114,112 code points:

```
code points PyYAML forbids but the guard ACCEPTS : 0
code points the guard rejects that PyYAML tolerates (stricter, safe): 963003
```

Every code point that would break the header is one `str.isprintable()` refuses. The guard is
sufficient across the entire range, not just the 38 the producer tested.

**P2 — soundness end to end.** 12,363 code points (all of `0x0000`–`0x2FFF`, the separators, BOM,
noncharacters, lone surrogates, a sample from each higher plane) through the real `_render_markdown`
and `yaml.safe_load`: `rejected=1244, clean_round_trip=11119`, and `0` unparseable, `0` value-mutated,
`0` key-injected. Baseline frontmatter keys were derived from the renderer, not hand-written, so the
"injected key" false positive from R1 cannot recur.

**P3 — no false positives.** Ordinary claim text survives: colons, `#`, brackets, parentheses,
apostrophes, `%`, accented Latin, `->`. The 963,003 extra rejections are unassigned and format
code points, which a claim sentence has no use for.

### The guard is reachable in production

This is the failure mode from BEAAA-21755 (a guard that cannot fire), so I traced it rather than
assuming. `--focus-claim` is `required=True` (line 1069) → `run_gate` (1096) → validator (651) →
report (766) → the single receipt writer (821). The validator runs before any file write, and there
is exactly one `_render_markdown` call site. No bypass.

### F2 substance verified independently

The rewritten citation is truthful. `artifact-metadata-schema.md` is real, present in 22 of 24 seat
instruction directories, and it names the traceability field **`source_refs`** — not `ticket_refs` —
requiring that refs point to retrievable evidence. `ticket_refs` is genuinely the receipt
frontmatter's own field: it is on `contracts/are-scanner-api-v1.md:9`, and all 16 receipts actually
produced by this generator carry it.

I checked the comment's claim that `ticket_refs` is carried by "every receipt in this series" before
treating it as an overclaim. 9 files in `tools/calibration-deltas/` dated at or after v0.1 lack the
field, but every one is a hand-written delta with no frontmatter at all; of the 16 receipts this
generator emits, none lack it. The claim holds. Not a finding.

BEAAA-23845's description is also corrected — its only surviving mention of the path is the
cross-reference naming this defect, which is intentional.

### AC7 measurement is real, and honestly framed

Both sides produced byte-identical output matching the producer's claimed SHAs and the committed
artifacts:

```
30640332d36def1467ddfff7a783142842fe7e37a95d8e7b5f5e61e837d7597d  receipt .md   (cand tree, base tree, committed)
3d0b741169e862e49f74c0fa687a9afdf404196b6bd2f0b06d42319253f978ac  delta  .json  (cand tree, base tree, committed)
```

8 agents, `passed=True`, Δ 0.0 on total and all four pillars, spread 0 both sides, approval
provenance PASS, `failure_reasons=[]`, 24 frozen input hashes. Because the receipt self-reports
`Candidate commit/tree: self / self`, I settled ±0 by regenerating from two genuinely different
trees rather than trusting the committed file.

The gate exits 1, from the cosmetic final `print(md_path.relative_to(REPO))` when `--out-dir` is
outside the repo. That happens after both artifacts are written; it is an artifact of my invocation,
not a gate failure.

Δ=0 here is expected by construction — the change touches no scoring code, and the gate replays
frozen inputs rather than running live scans. Its value is reproducibility across two trees. The
receipt is honest about this and does not overstate it.

### Test rename is safe

R2 renames `test_an_unrejected_double_quote_would_break_the_frontmatter`. Renaming a test can
silently kill CI gates that pin pytest node ids, so I swept `scripts/ .github/ ops/ tools/ bin/`
for `test_release_gate_n5_beaaa10187.py::` pins and for the old name: no hits anywhere. The old
name existed only on the two BEAAA-23953 branch commits, never on master.

---

## F-A (BLOCKER) — the candidate does not merge into current master, and 2 of its 3 new tests fail on the merge result

**Severity: high.** The tree that would ship is not the tree that was tested, and on the shipped
tree the two tests AC5 relies on to prove the guard is load-bearing stop exercising the guard.

Master moved from `40abb5cb` to `b4344001` while this fix was in review, gaining `cc0fdcfe`
(BEAAA-23957, rubric_point_id parameterisation). That commit edits the same two files.

**Repro:**

```
git worktree add -f --detach /tmp/m b4344001
cd /tmp/m && git merge 4e68daac75247660c59f305dbec929cf12e16a6d
# CONFLICT (content): Merge conflict in scripts/release_gate_n5.py
# CONFLICT (content): Merge conflict in tests/test_release_gate_n5_beaaa10187.py
# 1 hunk in the script, 2 in the tests
```

The script hunk is two independent additions in the same region and resolves by union. The test
hunks are interleaved and need a hand resolution. After resolving all three faithfully:

```
2 failed, 45 passed
  test_a_rejected_double_quote_would_have_broken_the_frontmatter
  test_no_accepted_focus_claim_survives_the_header_changed_or_broken
TypeError: _validate_receipt_provenance() missing 1 required keyword-only argument: 'rubric_point_id'
```

**Cause.** BEAAA-23957 made `rubric_point_id` a required keyword-only parameter of
`_validate_receipt_provenance`. R2's two new tests call that validator directly with only
`issue`, `cto_cosign_ref`, `focus_claim` and `ticket_refs`.

**Why it matters beyond a red suite.** These are exactly the two tests AC5 cites to show the guard
is load-bearing — the point N2 was raised to fix at R1. On the merged tree they raise `TypeError`
before reaching the guard, so they no longer prove anything about it. A red suite that fails for
the wrong reason invites a resolution that deletes or weakens them, which would return the guard to
the unproven state R1 was rejected for.

**Expected vs actual.** Expected: the candidate merges and all 37 tests pass on the shipped tree.
Actual: three conflict hunks, and 2 of the 3 new tests fail with `TypeError`.

**What a correct rebase needs:** rebase onto `b4344001`; pass `rubric_point_id=[...]` (or
`release_gate_n5.RUBRIC_POINTS_NOT_EXERCISED`) in both direct validator calls; re-run AC5's mutant
on the new base and re-state the expected failure count, since the parametrize list grows by
master's five `rubric_point_id` cases; re-run AC7 from the new base. Hand back the new SHA.

Note the earlier fixture worry is *not* a problem: `40abb5cb` already carried `rubric_point_id` as a
hardcoded value, so `_renderable_report()` satisfies master's new required-key check in
`_render_markdown`. I checked and discarded that hypothesis.

## F-B (BLOCKER) — F2 is only two-thirds done on the shipped tree; the phantom citation is live on master again

**Severity: medium.** AC6 fails on the merge result, and the deliverable F2 exists to achieve is not
achieved on the tree that ships.

Phantom-citation count in `scripts/release_gate_n5.py`:

```
40abb5cb (base)      2
017be5ef / 4e68daac  0     <- candidate removes both
cc0fdcfe             3     <- BEAAA-23957 adds a third
b4344001 (master)    3
merge result         1     <- AC6 FAILS
```

The survivor is at `b4344001:scripts/release_gate_n5.py:606`, in the new fail-closed message:

```python
raise ValueError(
    "rubric_point_id must list the rubric points this run exercised, or "
    f"{RUBRIC_POINTS_NOT_EXERCISED!r} when it exercised none; it is "
    "required under canon/artifact-metadata-schema.md"
)
```

`git show cc0fdcfe -- scripts/release_gate_n5.py` shows this line added (`+`). The pattern was
copied from the old `ticket_refs` message that F2 removes, so an operator who trips the
`rubric_point_id` guard is still sent to a path that does not exist in this repository — the exact
defect F2 was filed for, one guard over.

**Ownership, stated plainly:** this was introduced by BEAAA-23957 `cc0fdcfe`, which I approved on
BEAAA-23963. I did not catch it there. It belongs in this ticket because this is the ticket that
owns removing the citation, and the rebase is already touching these lines.

**Fix:** during the rebase, extend F2's correction to the `rubric_point_id` message, citing the
receipt schema the requirement actually comes from, as the other two messages now do. Then AC6 holds
on the merge result.

---

## Non-blocking notes

**N1.** Roughly fourteen live-looking citations of `canon/artifact-metadata-schema.md` remain
outside `scripts/` and `tests/` — `docs/adr-0008-project-dir-rename-bmi-to-are.md:200`,
`tools/are-scan-smoke-post-rename-bmi-to-are.md:140`,
`docs/beaaa-461-architecture-flags-proposal.md:119`,
`engineering/probe-sdk/beaaa-5056-r1-build-v0.1.md:256`, `brand/README.md:61` and others. Same
phantom-path class, out of this candidate's scope. Worth its own ticket; I am not folding it in.

**N2.** Canon distribution gap: the schema is in 22 of 24 seat instruction directories and one copy
diverges by sha256. My own QA seat still lacks it. Scanner Engineer #2 was right to decline folding
this in, and offered location evidence. I will raise it separately.

**N3.** The guard rejects 963,003 code points PyYAML would tolerate (unassigned and format
categories). Deliberate, fail-closed, and P3 shows no false positives on ordinary claim text.
Recording it so nobody later "fixes" it by loosening the check back toward a range.

**N4.** The module-level comment block explaining the `isprintable()` choice sits above
`_validate_receipt_provenance` while the code it explains is inside the function. Readable as is;
purely a placement observation.

---

## Commands run

```
git fetch <remote> agent/beaaa-23953-focus-claim-escape        # candidate 4e68daac
git worktree add -f --detach /tmp/qa23964r2/cand 4e68daac...
git worktree add -f --detach /tmp/qa23964r2/base 40abb5cb
python3 -m pytest tests/test_release_gate_n5_beaaa10187.py -q                    # 37 / 29
python3 -m pytest tests/test_contract_pin_reconciliation_beaaa18467.py \
                  tests/test_warranty_recipient_integrity_beaaa12031.py -q       # 18
python3 qa_r2_unicode_property.py                                                # P1, P2, P3
python3 qa_r2_c1_regression.py                                                   # AC3, AC4
# AC5: remove the offenders block only, then re-run                              # 7 failed, 30 passed
bash ac7_run.sh                                                                  # AC7 byte identity
git worktree add -f --detach /tmp/qa23964r2/merge b4344001
git merge 4e68daac...                                                            # F-A: 3 conflicts
python3 -m pytest tests/test_release_gate_n5_beaaa10187.py -q                    # F-A: 2 failed, 45 passed
grep -rn "artifact-metadata-schema" scripts/ tests/                              # F-B: 1 hit
```

Probe scripts committed alongside this report: `qa_r2_unicode_property.py`,
`qa_r2_c1_regression.py`, `ac7_run.sh`.
