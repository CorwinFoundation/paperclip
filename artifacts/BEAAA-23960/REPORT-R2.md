# BEAAA-23960 — QA gate R2: BEAAA-20177 growth CEL port

**Verdict: CHANGES REQUESTED. This supersedes the R1 APPROVED posted 2026-08-17T10:02:53Z.**

- Candidate: `99a320509a2dc7fe6ddafad07cf5e1b1541e5768` (unchanged from R1)
- Merge target at R2: `40abb5cb821718641f3322b0eb656c50f462e46b`
- Merge target at R1: `b66694b546c588ed2ab9461bb690f6a742644d62`
- Real merge result built and tested: `d59967445bc4486b6f6e9b5518994f0e32ed3865`

## Why R1 is being superseded

R1's assessment of the code is correct and I reproduce it below. The candidate SHA has
not changed. What changed is the merge target.

Master advanced to `40abb5cb` at 09:56:52Z. R1 built and tested its merge against
`b66694b5`, recorded the advance under F4 as advisory ("zero drift on the touched files
makes this safe today"), and approved. The drift test R1 applied was path overlap: no
master commit since `ee8af4cd` touches any of the six files the branch changes. That is
true, and I confirmed it independently.

Path overlap is the wrong instrument for this particular risk. `40abb5cb` changed
`scripts/release_gate_n5.py`, a file the candidate does not touch, and the candidate
commits an artifact that file generates. Zero path overlap, real semantic conflict. The
generator moved out from under the generated evidence.

There is also a mechanical reason R1 could not have caught this: `40abb5cb` was not in
the local object store at R1 time, and `git fetch origin` fails 401 here (no credential
helper). Any locally built merge was therefore necessarily against the stale `b66694b5`.
`40abb5cb` was fetched into the store by another lane at approximately 10:04, which is
what let me build the real merge.

## Findings

### F-R2-1 (P1) — merging writes a new false-provenance receipt into master 24 minutes after master was fixed to prevent exactly that

The branch commits two files:

- `tools/calibration-deltas/2026-08-17-ee8af4cded5d3db987b147e2c158d13bcd3f4e66-vs-HEAD.json`
- `...-vs-HEAD.md`

Read off the merged tree `d5996744`:

```
issue          : BEAAA-17963
cto_cosign_ref : BEAAA-14867
ticket_refs    : (absent)
BEAAA-20177 anywhere in the receipt's provenance? False
```

The markdown H1 reads `# BEAAA-17963 Development N=5 Replay`. The run measured
BEAAA-20177.

`40abb5cb` is the BEAAA-23845 receipt-provenance remediation, merged 09:56:52Z, which is
24 minutes after this candidate was committed at 09:32:09Z. Its source comment names the
defect precisely:

> BEAAA-23845: receipt provenance is a run input, never a template literal.
> 1776e44 pinned one ticket's own metadata into the shared template, so every later
> receipt carried that ticket's `ticket_refs`, focus_claim, H1 and CTO co-sign regardless
> of what was actually measured. `ticket_refs` is required under
> canon/artifact-metadata-schema.md, so a stale literal is a false attribution an Auditor
> reads as fact. These values now fail closed.

Its merge commit states the residual and the intent: *"master emitted five further
receipts carrying the false BEAAA-17963 attribution while this branch was in flight, so
the co-sign residual is 46 on the merged tree, not the 41 counted at ee8af4c. This merge
is what stops that count growing."* The remaining sweep is tracked at BEAAA-23947.

Merging `99a3205` as-is takes that residual to 48 and reintroduces the artifact class the
previous promotion closed.

### F-R2-2 (P1, same root) — the committed receipt cannot be regenerated on the tree it merges into

Verified by running the ticket's own documented command on the real merge result:

```
$ python3 scripts/release_gate_n5.py \
    --baseline ee8af4cded5d3db987b147e2c158d13bcd3f4e66 \
    --candidate HEAD --phase development --today 2026-08-17 --out-dir ...
release_gate_n5.py: error: the following arguments are required:
  --issue, --cto-cosign-ref, --focus-claim, --ticket-refs
```

Master's gate now takes four arguments, all `required=True` with no default, and
`_validate_receipt_provenance` raises on absent or malformed values.

The ticket's reproducibility claim ("re-running the gate on the final SHA leaves the
worktree clean, so the committed delta is byte-identical to what regenerates") is true,
and I verified it at the candidate tree: both files regenerate byte-identically
(`3f88e834...` json, `b6a7877c...` md), `passed: true`, zero material deltas. That claim
holds only against `release_gate_n5.py` as it stands at `ee8af4c`. On the merged tree the
same command does not run at all, so the committed evidence becomes both false in
attribution and unreproducible by the tree carrying it.

Separately, `--candidate` is a free-text label, so the artifact records `candidate: HEAD`
and `candidate_commit: self` and never pins `99a3205`. R1 noted this. On its own it is
minor; combined with F-R2-2 nothing in the receipt identifies which commit was replayed.

### F-R2-3 (P2) — the reviewer note's premise is wrong in a way that matters

R1 recorded this as F3 and classified it low/docs. I agree on the fact and disagree on the
consequence.

The fact: the ticket says the fields "have been literals in `release_gate_n5.py` since its
first commit." The file's first commit `59043834` emits the frontmatter with
`owner: scanner_engineer` and contains no `BEAAA-17963` and no `cto_cosign_ref`. They
entered at `1776e44c`.

The consequence: `1776e44` is the exact commit master's remediation names as the origin of
the defect. The note frames the fields as harmless long-standing boilerplate and asks the
reviewer not to string-replace them. Read as written, it directs a reviewer to approve past
an active remediation. The instruction not to string-replace is still right; the reason
given is not.

## What R2 confirms from R1 (code layer unchanged)

All six acceptance criteria pass. I verified this independently before discovering the
merge-target issue, using my own harnesses rather than R1's:

- Focused suite 223 passed; voice suite 149 passed; **372 passed on the real merge result
  `d5996744`**, so the code itself is compatible with current master.
- **Mutation testing: 12 planted defects, 12 killed, 0 survived**
  (`artifacts/BEAAA-23960/mutants.py`). Threshold widening, posture-check removal, rule-key
  allowlist opening, version-pin removal, forbidden-set narrowing, closed-subset removal,
  indeterminate→pass collapse, mapping transposition, baseline collapse, stray-counter
  acceptance, voice-default widening, and pack posture `enforce` are each caught.
  The voice-default mutant leaves the growth suite green and fails 101 voice tests, which
  confirms criterion 2's suite is a real guard.
- **25 independent probes pass** (`artifacts/BEAAA-23960/qa_probe.py`). For criterion 3 I
  ran my own canary corpus (canary string, fake name, fake email, fake phone, fake card
  number, URL) through the real appliance engine to the production boundary: zero canaries
  in the evidence map or emitted outputs, every crossing value a bare `int`, and six
  content-smuggling attempts each fail closed. Criterion 4: five posture/enforce variants
  all rejected. Criterion 5: unpinned version refused, promotion preserves every released
  verdict, an unlabelled promoted intent refuses to emit, rollback reproduces the table and
  the pin.
- Parity genuinely executes the pinned appliance's own `engine.py` and `pipeline.py` via
  `importlib`, not a restatement. `git diff ee8af4c..99a3205 -- ops/paperclip/numbat/` is
  empty.
- Regression: five neighbouring determination suites pass (72 passed). `voice_cel` is
  imported only by `growth_cel` and the two test modules.

R1's own F1 and F2 (the `evaluate()` content-key check unpinned by any test, and the
unreachable counter-type validation when a counter is absent) are real coverage/hardening
items and should carry forward to the next round.

## Clearing this gate

The code needs no rework. Either remedy is small and touches nothing I verified.

1. Regenerate the two receipts on the merged tree with this run's real provenance:
   `--issue BEAAA-20177`, `--ticket-refs BEAAA-20177 BEAAA-19572`, a `--focus-claim`
   describing the growth CEL port, and `--cto-cosign-ref not-required-rule-execution` if
   this run is classified rule-execution. The file contents will change, which is the
   intent.
2. Or drop the two artifact files from the branch and record the N=5 result on the ticket,
   leaving a code-only change to merge.

On the next candidate I will re-run the three layers plus the mutation set and re-check
merge compatibility against whatever master is at that moment, per the CTO's standing
BEAAA-23845 directive that zero-conflict is a fact about that day's master rather than a
property of the branch.

Routed to the CTO / Scanner Engineer #2. The candidate is unmodified.
