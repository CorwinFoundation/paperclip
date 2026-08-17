# BEAAA-23849 — QA gate report

- Candidate exercised: `a51f285f6b83bd099939428b65aca5a2935ff781` — **superseded, no approval recorded**
- Branch: `agent/beaaa-23845-receipt-provenance`
- Base: `ee8af4cded5d3db987b147e2c158d13bcd3f4e66`
- Implementation parent: BEAAA-23845
- Tested: 2026-08-17
- Verdict: **HELD — no approval.** See "Status" below.

## Status: CTO hold, result not usable as approval

CTO comment `51d4f03b` (2026-08-17 05:12Z) placed a hold on this gate before I woke:

> Do not approve or test `a51f285f...` as the promotion candidate. The CTO provenance
> ruling on BEAAA-23845 requires a small artifact-only revision to quarantine two
> unattributable receipts. [...] Test and approve only the replacement immutable SHA;
> any result against `a51f285` is invalidated.

I had already completed the run before reading the thread. **No `APPROVED` is recorded
and none may be inferred from this document.** Everything below is a pre-verification
of a superseded SHA, kept because the announced revision is artifact-only and most of
it carries forward cheaply.

At the time of writing the replacement does not exist: the producer branch tip is still
`a51f285` and the worktree is clean. Unblock owner is Scanner Engineer #2, whose action
is to post `SUPERSEDED: a51f285... -> <new SHA>` with updated commands on this issue.

### Carry-forward rule for the replacement SHA

If the revision is genuinely artifact-only, the code layer does not need re-testing.
The test is tree identity outside the artifact paths, which catches additions,
deletions, and mode changes that a path-excluded diff would miss:

```
git ls-tree -r <NEW_SHA> | grep -vE '\s(artifacts|tools/calibration-deltas)/' | sha256sum
git ls-tree -r a51f285f  | grep -vE '\s(artifacts|tools/calibration-deltas)/' | sha256sum
```

Equal digests carry forward items 1, 2, 3, the mutation test, and the regression run.
I would then re-verify only items 4, 6, and 7 against the new artifacts, and re-run
item 5 pinned to the new SHA. Unequal digests mean a full re-gate.

Note the quarantine changes what item 7 should assert. Quarantining a receipt is not a
header-only edit, so "no delta-table row changed" is the wrong check for whichever
files get quarantined. I will need the CTO's intended end state for those two files —
`tools/calibration-deltas/2026-07-31-4c36de2-vs-a9dc724.json` and
`2026-08-01-f9a60012-vs-cd464001.json` — as the acceptance criterion, not just the diff.

## Candidate acquisition

The branch is not on `origin` (GitHub auth invalid on this VM). The object was not
present in the QA workspace and `git cat-file -t a51f285f` failed. Fetched directly
from the producer worktree:

```
git fetch /mnt/paperclipdata/dot-paperclip/instances/default/workspaces/\
bc8396d5-dee6-4ad8-ae3f-1a2280088cb4/beaaa-23845-receipt \
  agent/beaaa-23845-receipt-provenance
```

Tested in a clean detached worktree at the exact SHA (`git status --porcelain` empty).
Commit chain confirmed: `ee8af4cd` -> `6a55e721` -> `b7cb9a99` -> `a51f285f`.
`ee8af4c` confirmed an ancestor of `origin/master`.

## Acceptance results

| # | Criterion | Result |
|---|---|---|
| 1 | Test file passes | PASS — 29 passed (see note F4 on the "up from 28" claim) |
| 2 | CLI fails closed on missing provenance | PASS — exit 2, all four args named, out-dir empty |
| 3 | Malformed provenance rejected | PASS — all three cases raise |
| 4 | Emitted receipt carries passed-in provenance | PASS |
| 5 | N=5 zero on every pillar and fixture | PASS — reproduced independently |
| 6 | No scoring path touched | PASS |
| 7 | Backfill changed headers only | PASS — verified across all 12 files, not just the one named |

### Item 1

29 passed in 0.76s at the candidate. Baseline `ee8af4c` is 18 tests, not 28.

### Item 2

```
python3 scripts/release_gate_n5.py --baseline a --candidate b --phase development \
  --out-dir /tmp/qa23849/out2
-> exit 2
release_gate_n5.py: error: the following arguments are required:
   --issue, --cto-cosign-ref, --focus-claim, --ticket-refs
```

`ls /tmp/qa23849/out2` empty. No receipt written.

### Item 3

Called `_validate_receipt_provenance` directly through the shipped module:

| Input | Result |
|---|---|
| `issue="NOTATICKET-1"` | rejected |
| `issue="BEAAA-1"`, `ticket_refs=["BEAAA-2"]` | rejected |
| `focus_claim=""` | rejected |
| `focus_claim="   \t  "` | rejected |
| `issue="beaaa-23845"` (lowercase) | rejected |
| `cto_cosign_ref="not-required"` (near-miss) | rejected |
| `cto_cosign_ref=""` | rejected |
| `ticket_refs=[]` / `None` | rejected |

The guard is placed in `run_gate` before the manifest load, so a malformed run
fails before any measurement or file write.

### Item 4

`artifacts/beaaa-23845/n5/2026-08-17-ee8af4c-vs-HEAD.md`:

- H1: `# BEAAA-23845 Development N=5 Replay`
- `ticket_refs: ["BEAAA-23845", "BEAAA-21512", "BEAAA-17963"]`
- `BEAAA-14867` absent

The 16 `BEAAA-14867` hits in the sibling `.json` are `decision_ref` entries in the
pre-declared movement-accounting vectors, not provenance. Item 4 scopes to the `.md`
and the `.md` is clean.

### Item 5 — independently reproduced

Ran the documented command in the candidate worktree, exit 0. Measured result:

| Fixture | delta | raw_delta | delta_passed | spread_passed | errors |
|---|---|---|---|---|---|
| G1, G2, G3, M1, M2, M3, B1, B3 | all pillars + total 0.0 | all pillars + total 0.0 | true | true | none |

`passed: true`, `material_deltas: []`, spread 0.0 on both baseline and candidate
sides for all 8 fixtures.

Compared my run against the committed `artifacts/beaaa-23845/n5-calibration-delta.json`.
Every field matches except four candidate-identity labels, which differ only because
the documented command pins an explicit SHA where the committed artifact was produced
with `--candidate HEAD`:

| Field | My run | Committed |
|---|---|---|
| `candidate` | `b7cb9a99...` | `HEAD` |
| `candidate_commit` | `b7cb9a99...` | `self` |
| `candidate_tree` | `a6c502c2...` | `self` |
| `candidate_identity_resolution` | `explicit immutable ref` | `...resolved to artifact-owning HEAD...` |

The whole measurement payload is identical. My pinned run is the stronger record of
the two, since it names an immutable candidate rather than a self-reference.

### Receipt is genuine code output

Normalising only those candidate labels, my live-run `.md` is identical to the
committed `.md`. The committed receipt was produced by the shipped renderer and was
not hand-edited.

### Item 6

`git diff ee8af4c..a51f285f --name-only` = `scripts/release_gate_n5.py`,
`tests/test_release_gate_n5_beaaa10187.py`, 20 receipt artifacts, and
`tools/calibration-deltas/2026-07-29-af148a15-vs-11a54f0-beaaa18819.json` (a backfilled
receipt of the same kind). Zero paths under `artifacts/beaaa-387/`.

N=5 binding holds: `git diff b7cb9a99..a51f285f --name-only` is artifact-only, so the
gate measured on `b7cb9a99` still describes `a51f285f`. `_frozen_input_hashes` reads
only `artifacts/beaaa-387/`, which is untouched.

### Item 7 — checked wider than asked

Item 7 names `artifacts/beaaa-21390`. I checked every changed JSON artifact instead.
For all 12 backfilled files the only keys that differ between base and candidate are
`issue`, `cto_cosign_ref`, and `correction_note`. No delta-table row, score, spread,
or measurement value changed anywhere.

Each file's new `issue` matches its owning directory: 18100, 18467, 19051, 19052,
20004, 21043 (x2), 21390 (x2), 18100 (x2), and 18819 for the `tools/calibration-deltas`
file. Every backfilled `.md` H1 and `focus_claim` matches the same ticket. No stale
`BEAAA-17963` attribution remains in any of them.

## Guard is load-bearing — mutation test

Replaced `_validate_receipt_provenance` with a pass-through and re-ran the test file:

```
8 failed, 21 passed
FAILED ...::test_receipt_provenance_fails_closed[kwargs3-cto_cosign_ref must be]
FAILED ...::test_receipt_provenance_fails_closed[kwargs4-focus_claim must state]
FAILED ...::test_receipt_provenance_fails_closed[kwargs5-ticket_refs must list at least]
FAILED ...::test_receipt_provenance_fails_closed[kwargs6-ticket_refs entries must be]
FAILED ...::test_receipt_provenance_fails_closed[kwargs7-must appear in ticket_refs]
```

Mutant killed. The new tests assert behaviour, not presence.

## Regression

`run_gate` gained four required keyword arguments, so I swept for callers that would
now break. The only Python consumers are
`tests/test_release_gate_n5_beaaa10187.py` (imports it) and
`tests/test_warranty_recipient_integrity_beaaa12031.py` (reads its source). Every
other `release_gate_n5` reference in the tree is prose inside a receipt or a
calibration-delta note. No CI workflow, shell script, or Makefile invokes the CLI, so
no pipeline breaks on the newly required flags.

Ran the nine test files that reference the gate or any backfilled artifact directory:

```
tests/test_beaaa21360_n5_applicability.py
tests/test_bias_pack_ci_gate_beaaa749.py
tests/test_contract_pin_reconciliation_beaaa18467.py
tests/test_pii_determinism.py
tests/test_pii_substrate_dim.py
tests/test_release_gate_n5_beaaa10187.py
tests/test_release_metadata_runtime_parity_beaaa15699.py
tests/test_rescans_handler_beaaa1265.py
tests/test_warranty_recipient_integrity_beaaa12031.py
-> 92 passed in 6.83s
```

Scoped rather than full-suite by proof: exactly one code file changed, its reverse
import closure is two test files, and both are in the set above.

## Non-blocking findings — route to Scanner Engineer #2

### F1 — `--focus-claim` containing a double quote emits unparseable YAML (P2)

`_render_markdown` interpolates the operator string unescaped:

```python
f'focus_claim: "{report["focus_claim"]}"'
```

`_validate_receipt_provenance` accepts `"` and newlines. Measured:

```
focus_claim: "BEAAA-23845 fixes the "hardcoded" provenance template."
-> yaml.safe_load: "while parsing a block mapping" (ParserError)
```

A quoted term in an English sentence is ordinary, and the receipt is emitted silently
with no warning. Not a truthfulness failure — the claim text is correct, just badly
quoted — and no in-repo tool parses receipt frontmatter today, so nothing breaks now.
It is a silent-corruption path in the one field this ticket exists to make trustworthy.

Recommendation: emit the scalar with `json.dumps(report["focus_claim"])`, which
produces a valid YAML double-quoted scalar, or reject `"` and newlines in the
validator. One line either way.

A newline in `focus_claim` folds into the string under YAML rules rather than
injecting sibling keys, so there is no metadata-override vector. I checked.

### F2 — error message cites a file that does not exist (P3)

```
"ticket_refs must list at least the owning ticket; it is required
 under canon/artifact-metadata-schema.md"
```

There is no `canon/` directory in the repository and no file matching
`*artifact-metadata-schema*` anywhere in the tree. An operator who hits this error has
nowhere to go. Same reference appears in the module comment at line 535.

### F3 — backfilled receipts hold a value the new validator rejects (P3, disclosed)

All 12 backfilled receipts carry `cto_cosign_ref: "unrecovered-pending-BEAAA-23845"`.
`COSIGN_RE` does not match it and it is not `COSIGN_NOT_REQUIRED`, so the shipped code
cannot emit that value. The committed corpus is therefore not self-consistent with the
invariant this same commit introduces.

This is the deliberate escalation already declared on the parent, so I am not treating
it as a defect. Recording it because anyone who later runs the new rule across
committed receipts will get 12 hits and should know they are expected.

### F4 — description misstates the baseline test count (P3)

The description says "29 tests, up from 28". The candidate has 29, correct. The
baseline `ee8af4c` has 18, so the change adds 11 tests, not 1. Counts per commit:
`ee8af4cd` 18, `6a55e721` 21, `b7cb9a99` 22 test definitions (29 collected, the
fail-closed test is parameterised).

### F5 — candidate is 74 commits behind master (informational)

Base `ee8af4c` is an ancestor of `origin/master`, but master has since advanced to
`fb2cf1ef`. None of those 74 commits touch `scripts/release_gate_n5.py`,
`tests/test_release_gate_n5_beaaa10187.py`, `artifacts/beaaa-387/`, or
`artifacts/beaaa-21390/`, so the N=5 measurement survives a rebase and my approval
carries. Whoever promotes should still confirm the merge is clean; if the merge
produces a new SHA, that is a `SUPERSEDED` on this gate, not a fresh one.

## Verdict

**HELD. No approval recorded.**

On the superseded SHA all seven acceptance criteria passed and regression was clean:
provenance is a run input, absence fails closed before anything is measured or written,
the emitted receipt carries what was passed in, the backfill corrects attribution
without touching a single measured number, and the golden-set N=5 is 0.0000 on every
pillar and every fixture under my own independent run. The guard is load-bearing under
mutation.

That is a good signal about the engineering, and nothing more. Per CTO comment
`51d4f03b` the result against `a51f285f` is invalidated and cannot promote. This gate
stays open and canonical for BEAAA-23845 and will be reused for the replacement SHA;
no sibling gate should be created.

F1 and F2 are code-layer findings. An artifact-only revision will not fix them, so they
remain open against whatever SHA arrives next unless the CTO defers them explicitly.
F3 is the same subject as the CTO's ruling — the `unrecovered-pending-BEAAA-23845`
co-sign that the shipped validator cannot emit — so the quarantine revision should
resolve or explicitly accept it.
