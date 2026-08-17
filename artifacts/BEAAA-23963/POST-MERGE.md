# BEAAA-23963 post-merge verification — merge `b4344001` on canon master

Written after the CTO merged BEAAA-23957 and reported that my evidence commit was not
reachable from origin. Two things checked here: that the merge that shipped is the tree
I approved, and that the evidence trail is now published.

## The shipped merge is the tree I gated

| Check | Result |
|---|---|
| Canon master tip, fetched from origin | `b434400107829333173e08180280cf09cd366b1d` |
| Parents | `40abb5cb` (first), `cc0fdcfe` (second) — the exact base and candidate I gated |
| Files changed vs first parent | `scripts/release_gate_n5.py`, `tests/test_release_gate_n5_beaaa10187.py`, nothing else |
| `scripts/release_gate_n5.py` blob | `4653c834` — identical to the candidate and to the hash I derived independently |
| `tests/test_release_gate_n5_beaaa10187.py` blob | `54964d42` — identical |

The strongest form of the claim: the merge result I computed during the gate was
`581f9a1c`. Its tree is `61d2e1fe02069b07eef2ff31dd953f8646f09bd2`. The tree of the
merge that actually shipped, `b4344001`, is **the same object**. Different commit,
byte-identical tree. Nothing entered the tree between approval and promotion.

## The defect is gone on the shipped tip

Zero occurrences of `GOVERNANCE.PII.HANDLING_ATTESTATION` or
`ROBUSTNESS.PII.SCAN_TIME_DETECTION` in the merged generator, in either quoting form.
`rubric_point_id` on the tip is a required parameter, validated at lines 564–619 and
emitted from the caller's value at line 772.

## Tests re-run on the merge commit itself, not on a predicted merge

```
git worktree add --detach /tmp/qa_merge_verify b4344001
python3 -m pytest tests/test_release_gate_n5_beaaa10187.py -q                 # 39 passed
python3 -m pytest tests/test_release_gate_n5_beaaa10187.py \
                 tests/test_contract_pin_reconciliation_beaaa18467.py -q      # 41 passed
python3 -m pytest tests/test_release_gate_beaaa10189.py \
                 tests/test_beaaa21360_n5_applicability.py -q                 # 15 passed
```

The 41 in my approval and the 41 in the CTO's close-out are the same measurement over
the same two modules, and it reproduces on the merge commit. The adjacent release-gate
and N=5-applicability modules were not part of the original acceptance set; I ran them
here as extra regression and they are clean.

Baseline correction stands: 29 → 39, delta **+10**. The "33" in the BEAAA-23963
description was wrong, the CTO has accepted that on the ticket.

## Evidence trail now published

The CTO was right. My approval commit `71b1764e` existed only in one local worktree,
along with 30 earlier QA commits. An evidence trail that cannot be resolved from the
shared object store is the same click-through failure BEAAA-23957 was raised about.

All 31 commits are now on origin at `refs/heads/qa/evidence-trail`, tip `71b1764e`.
Confirmed server-side, not from my own clone: GitHub resolves the commit and serves
`artifacts/BEAAA-23963/REPORT.md` at that ref.

Pushed to a QA-owned branch, not to master. This is a branch creation, so no branch
protection applied and nothing was bypassed. It is an evidence ref and is not proposed
for merge; the artifacts are QA reports, harnesses and receipt A/B pairs.

Scanned the 31-commit diff (541 KB) for credentials and key material before pushing:
zero matches for GitHub tokens, PATs, private keys, AWS keys, Slack tokens or a
populated `PAPERCLIP_API_KEY`.

## Standing item for the next person in this file

`test_no_pii_rubric_point_literal_survives_in_the_generator` asserts on the
double-quoted form only. Mutant M2, a single-quoted re-hardcode, passes that specific
test and is caught only by the end-to-end assertion. The guard set holds today. The CTO
declined to widen a merged, twice-gated candidate for it, which I agree with. If this
file is opened again for any reason, make that assertion quote-agnostic.

Verified by QA, 2026-08-17, against origin.
