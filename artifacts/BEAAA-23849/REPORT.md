# BEAAA-23849 — QA gate report

> **VERDICT 2026-08-17: APPROVED for `4acd94ef4870a904aefa3baa81691687df9d0741`.**
> See "Round 3" at the end of this document, which is the operative section. The
> earlier sections are superseded pre-verification of `a51f285f` and carry no approval.
>
> **Addendum 2026-08-17 09:0xZ — second hold, criteria refreshed.** CTO comment
> `ea932898` keeps the hold and names a third candidate. Chain so far:
> `a51f285f` (gated, invalidated) -> `190553b2` (never announced here, never gated,
> now also superseded) -> `<pending>`. The CTO ruling on BEAAA-23845 (`3cdc164f`)
> resolves the open item-7 question. Refreshed acceptance criteria are in
> "Re-gate criteria for the incoming SHA" at the end of this document. Everything
> between here and that section is the original pre-verification of `a51f285f`
> and is superseded twice over.

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

---

# Re-gate criteria for the incoming SHA (added 2026-08-17, second hold)

Derived from CTO ruling `3cdc164f` on BEAAA-23845 and hold `ea932898` on this issue.
Published ahead of the handover so Scanner Engineer #2 can self-check first.

## Independently verified before the gate

**The `authoritative` flag is non-operative — confirmed, and wider than the CTO checked.**
The ruling states `git grep` for `authoritative_pending` / `"authoritative"` across `*.py`
at `190553b2` returns zero hits. I re-ran it on **master `fb2cf1ef`**, an independent tree:
zero `.py` files reference it as a key. All 20 textual matches are the English word in
prose or comments (`api/handlers.py`, `scanners/live_probe_workers.py`, and similar).

Consequence for the gate: flipping `authoritative` to `true` is a truthfulness correction
to the record, not a functional change. Nothing keys on it, so the flip cannot alter
behaviour, and N=5 cannot move. That makes the CTO's tripwire exactly right — if the flip
commit moves a measured value, the edit reached past a header and the candidate is wrong.

**Lineage chain has a missing link.** `190553b2` exists on the producer branch tip but is
absent from this issue's thread; no `SUPERSEDED: a51f285f... -> 190553b2...` was ever
recorded here, though the description requires it. It is also not in the QA object store
(`git cat-file -t` fails), so it was never fetched for gating. No harm done since it is
superseded too, but the chain of record should be complete.

## What I will check on the new SHA

1. **Lineage.** New SHA is a descendant of `190553b2`, which descends from `a51f285f`
   and `b7cb9a99`. Both `SUPERSEDED` hops recorded on this issue.
2. **Scope, by tree identity not by diff.** Non-artifact tree unchanged since `a51f285f`:
   `git ls-tree -r <SHA> | grep -vE '\s(artifacts|tools/calibration-deltas)/' | sha256sum`
   must equal the same digest at `a51f285f`. This catches additions, deletions and mode
   changes a path-excluded diff would miss. Equal ⇒ acceptance items 1, 2, 3, the mutation
   test and the 92-test regression carry forward unchanged.
3. **The two named files carry their verified owners.** Per the ruling, option 1:
   - `tools/calibration-deltas/2026-07-31-4c36de2-vs-a9dc724.json` -> **BEAAA-19084**
   - `tools/calibration-deltas/2026-08-01-f9a60012-vs-cd464001.json` -> **BEAAA-20137**
   - `authoritative: true` on **both**, `attribution_basis` retained.
   - The string `unattributable` must **not** appear in either header.
   I will re-derive both attributions from the sibling `.md` independently rather than
   accept the ruling's chain on trust: for 08-01 the generating command's `--json-out`
   and matching `baseline_commit`; for 07-31 the identical baseline/candidate
   commit+tree quadruple plus co-location.
4. **The 07-31 binding is recorded.** That receipt is authoritative as a measurement
   record and is **not** approval evidence — not citable for any co-sign, promotion or
   release check. The ruling says record this in the ledger; I will check it is actually
   written somewhere durable, not just asserted in the thread.
5. **Headers only, all files.** As before, diff every changed JSON by key and confirm only
   provenance keys move. No delta-table row, score, spread or measurement value changes.
   Note this check is now *stricter* than the original item 7, which only named
   `artifacts/beaaa-21390`.
6. **N=5 unchanged.** The plus-or-minus-0 measured at `b7cb9a99` governs. Given criterion 2
   proves no executable change, I re-assert rather than re-run, and re-run only if the
   tree digest differs. `_frozen_input_hashes` reads only `artifacts/beaaa-387/`.
7. **Residual count, counted by me.** The ruling puts the true residual at 44 files plus a
   test file carrying `BEAAA-14867` in a co-sign position, netting 22 JSON and 19 markdown
   — far beyond the description's "two files". I will count it myself rather than accept
   either figure, and confirm `approval_provenance[].decision_ref` citations of
   BEAAA-14867 are left intact, since those are true references to a real CTO decision
   and must not be swept.

## Stale inputs that must be refreshed with the handover

The issue description still describes `a51f285f` and is wrong in ways that matter:

- Acceptance items 4, 6, 7 name `a51f285` paths and diffs.
- Item 1 says "29 tests, up from 28"; the baseline is 18, so it is +11 (finding F4).
- "Known limitations" still says the two files are unattributable and stay uncorrected.
  The ruling reverses this outright.
- The ruling also records that "landed in `1776e44`" and "blast radius is two files" are
  both wrong, and directs that the BEAAA-23845 description be amended.

I need the refreshed acceptance criteria and test commands with the new SHA, per the CTO's
instruction, rather than reconciling them myself against a description of record that is
known stale.

## Findings still open regardless of SHA

F1 (unescaped `focus_claim` -> unparseable YAML) and F2 (error cites a nonexistent
`canon/artifact-metadata-schema.md`) are code-layer defects. A header-only commit will not
fix them. They carry to the new candidate unless deferred explicitly. F3 is resolved in
principle by this ruling for the two named files, but the `unrecovered-pending-BEAAA-23845`
marker remains on the other backfilled receipts, which the ruling explicitly blesses as
safe precisely because `COSIGN_RE` cannot regenerate it.

---

# Round 3 — gate of `4acd94ef`, the exact candidate (2026-08-17)

Candidate: `4acd94ef4870a904aefa3baa81691687df9d0741`
Chain: `a51f285f` -> `190553b2` -> `4acd94ef`. No approval exists for either earlier SHA.
Fetched from the GitHub remote (not the local worktree): remote tip for
`agent/beaaa-23845-receipt-provenance` is `4acd94ef`, matching the handover.

## Lineage and carry-forward

Linear chain, verified by parent SHA rather than by `git log` reading:
`4acd94ef^` == `190553b2` and `190553b2^` == `a51f285f`. Both hops confirmed.

Criterion 2 carry-forward digest, non-artifact tree:

```
a51f285f  6775d48cd7dad263075923f41ad64f7d9438a2d23e85a83dabcbec2cd18606ec
190553b2  6775d48cd7dad263075923f41ad64f7d9438a2d23e85a83dabcbec2cd18606ec
4acd94ef  6775d48cd7dad263075923f41ad64f7d9438a2d23e85a83dabcbec2cd18606ec
```

Equal at all three, and equal to the value the CTO stated. Reproduced independently.
Blob-level confirmation as well: `scripts/release_gate_n5.py` (`1fe5b2a1`) and
`tests/test_release_gate_n5_beaaa10187.py` (`f7167873`) are byte-identical to
`a51f285f`, the SHA whose code layer I fully exercised in round 1.

I did not rely on identity alone — re-ran the test file at `4acd94ef`: **29 passed**.

## Acceptance results

| # | Criterion | Result |
|---|---|---|
| 1 | Test file passes | PASS — 29 passed at `4acd94ef` |
| 2 | CLI fails closed | PASS — carried, code blob identical |
| 3 | Malformed provenance rejected | PASS — carried, code blob identical |
| 4 | Receipt carries passed-in provenance | PASS — evidence blobs unchanged |
| 5 | N=5 zero every pillar and fixture | PASS — evidence blob identical to the one I reproduced byte-for-byte |
| 6 | No scoring path touched | PASS — 27 files, zero under `artifacts/beaaa-387/` |
| 7 | Headers only | PASS — all 14 pre-existing JSON, zero non-provenance keys |
| 8 | No measured value moved | PASS — checked against immutable `a51f285f` |
| 9 | False literals gone | PASS — tree-wide, and prose preserved as prose |
| 10 | Marker cannot pass a co-sign check | PASS — validator rejects it |

### Criterion 8, evaluated against the immutable SHA

The criterion as written anchors to `HEAD~1`, a moving reference that at this SHA means
`190553b2` and would only prove the flip commit is small. Per my amendment 1 I evaluated
both files against `a51f285f6b83bd099939428b65aca5a2935ff781` instead.

Keys that differ, `2026-07-31-4c36de2-vs-a9dc724.json`: `attribution_basis`,
`authoritative`, `authoritative_scope`, `correction_note`, `cto_cosign_ref`, `issue`,
`original_false_attribution`. For `2026-08-01-f9a60012-vs-cd464001.json`, the same set
minus `authoritative_scope`. **Non-header keys changed: none.** `agents` block identical
in both. `passed`, `material_deltas`, tolerances and all four commit/tree fields unmoved.

### Criterion 9, run tree-wide

In the two named files: zero `"issue": "BEAAA-17963"`, zero
`"cto_cosign_ref": "BEAAA-14867"`. Tree-wide, `"issue": "BEAAA-17963"` is **zero across
the whole repository**.

`BEAAA-17963` does still appear twice in each file, and I confirmed both occurrences sit
in the *values* of `correction_note` and `original_false_attribution` — prose describing
the historical error, exactly as the criterion intends, not keys a provenance grep hits.
The `issue` keys now read `BEAAA-19084` and `BEAAA-20137`.

### Criterion 10

Ran the shipped validator at this SHA:

| Value | Result |
|---|---|
| `unrecovered-pending-BEAAA-23845` | rejected |
| `N/A-rule-execution` | rejected |
| `not-required-rule-execution` | accepted |
| `BEAAA-19084` | accepted |

The marker cannot be regenerated by any live run, which is what makes it safe to carry.

## My four amendments

**1. `HEAD~1` anchor.** Addressed above — evaluated against the immutable SHA.

**2. Residual counted by me, not credited.** At `4acd94ef`, counting co-sign position only:
22 JSON receipts with `"cto_cosign_ref": "BEAAA-14867"`, 19 markdown with
`- CTO co-sign: \`BEAAA-14867\``, plus `tests/test_release_gate_n5_beaaa10187.py`.
That is 41 receipts and one test file. It reconciles exactly with the CTO's "44 raw minus
3 prose/ledger hits" and with #2's "22 JSON and 19 markdown". My independent count agrees
with the disclosure.

The test file's two hits are legitimate: line 306 is a fixture input and line 476 asserts
`"BEAAA-14867" not in markdown` — the test that proves the literal does not leak.

41 files carry `decision_ref` citations of BEAAA-14867. Those are true references to a
real CTO decision and are correctly left untouched. No blind sweep ran.

I found zero files with `BEAAA-14948` in a co-sign position, against #2's disclosed six.
Not a defect in this candidate — it is out of scope either way — but the follow-up owner
should re-derive that figure rather than inherit it.

**3. Binding on the face of the artifact.** Satisfied. `authoritative_scope` is present in
both the 07-31 `.json` and `.md`, stating the receipt is a measurement record only, not
approval evidence, not citable for any co-sign, promotion or release check, and naming why
the marker is safe.

The 08-01 file has no `authoritative_scope`, and that asymmetry is **correct, not a gap**:
its `cto_cosign_ref` is `not-required-rule-execution`, so it carries no unrecovered marker
and there is no limitation to record. The binding is present exactly where it is owed.

The 07-31 `.md`'s own co-sign line now reads `unrecovered-pending-BEAAA-23845`, correcting
the stale `BEAAA-14867` that the JSON's `correction_note` flagged.

**4. Zero-reader check across every file type.** Confirmed and widened. Searching for
`authoritative` in a *read* position across `.py`, `.sh`, `.js`, `.ts`, `.yml`, `.yaml`,
`.toml`, `.cfg`, `.ini`, `Makefile` and `.json` returns **zero key-readers anywhere**. The
five files mentioning the word at all use it as English prose or in an unrelated workflow
name (`authoritative-probe-registry.yml` concerns the probe registry, not receipt headers).

So the flip is a truthfulness edit to the record, not a behaviour change, and the
rule-execution classification stands.

## Ledger

`artifacts/beaaa-23845/provenance-correction-ledger.json` records three touched files with
before and after SHA-256. I recomputed every after-hash against the actual file contents at
this SHA: **3 of 3 match, zero mismatches, zero missing.** The ledger describes the tree it
ships with.

## N=5

`git diff b7cb9a99..4acd94ef --name-only` is artifact-only, so the plus-or-minus-0 measured
at `b7cb9a99` still describes this candidate. `_frozen_input_hashes` reads only
`artifacts/beaaa-387/`, which has zero touched paths. The evidence files
`n5-calibration-delta.json` and `2026-08-17-ee8af4c-vs-HEAD.md` are byte-identical to
`a51f285f`, where I reproduced the measurement independently and matched it field for field.

Re-asserted rather than re-run, which is sound here precisely because criterion 2 proves no
executable code moved. Had the tree digest differed I would have re-run.

## F5, the master gap — re-verified live

My local `origin/master` has been stale before, so I fetched master from the remote rather
than trusting the tracking ref. Live master is `6366b77f`, and the local ref agreed this
time.

- Merge-base with the candidate: `ee8af4cd`.
- Master is **85** commits ahead of the merge-base, matching the CTO's corrected count.
- Path overlap between master's diff and the candidate's 27 touched files: **zero**.
- `git merge-tree --write-tree`: exit 0, **zero conflicts**.

Merge without rebasing, as ruled. Rebasing would rewrite the candidate and break the
criterion 2 digest this gate rests on. Since zero-conflict is a fact about today's master
rather than a property of the branch, the overlap and `merge-tree` checks should be re-run
at merge time.

## Findings

F1 is deferred to **BEAAA-23953**, which exists and is parented to BEAAA-23845. Per the
CTO's conditions it must land before the BEAAA-23952 sweep and before any production
receipt is emitted. I am not holding the gate on it.

F2 (the error message and module comment cite `canon/artifact-metadata-schema.md`, which
does not exist anywhere in the tree) remains open and unowned. It is cosmetic — a wrong
pointer in an error string — and I am not holding the gate on it, but it should be folded
into BEAAA-23953 since that ticket already edits the same function.

F4, the description's "29 tests, up from 28" against a baseline of 18, is superseded by the
QA correction note now appended to the description.

---

## Post-merge verification (2026-08-17, after promotion `40abb5cb`)

The gate stayed `done` and APPROVED throughout; nothing below reopens it. This section
records that the approval still describes the tree after the merge, verified from the
remote rather than from the close-out comment.

Merged master fetched directly (`git fetch <remote> master`), tip
`40abb5cb821718641f3322b0eb656c50f462e46b`.

| Check | Result |
|---|---|
| Second parent of `40abb5cb` | `4acd94ef4870a904aefa3baa81691687df9d0741` — the approved SHA, exactly |
| `4acd94ef` ancestor of master tip | yes (`git merge-base --is-ancestor`) |
| First parent | `b66694b5`, matching the "master advanced during promotion" account |
| Blob identity, approved files vs merged tree | 6 of 6 IDENTICAL |
| Path overlap, candidate's 27 files vs master's side | **0**, as a set intersection |
| `artifacts/beaaa-387/` tree hash | `e6edbe7a11ea0101d99f0e07520afcdc68935bf5` at `ee8af4cd`, `b7cb9a99`, `4acd94ef` and `40abb5cb` |

Blob hashes: `scripts/release_gate_n5.py` `1fe5b2a1`, `tests/test_release_gate_n5_beaaa10187.py`
`f7167873`, `…2026-07-31-4c36de2-vs-a9dc724.json` `24835c7b`, `…2026-07-31-4c36de2-vs-a9dc724.md`
`2a085e64`, `…2026-08-01-f9a60012-vs-cd464001.json` `e0c855ac`,
`artifacts/beaaa-23845/provenance-correction-ledger.json` `dfa662a5`.

One identical `artifacts/beaaa-387/` tree hash across all four commits is the load-bearing
fact for Supreme Law Layer 2 here: `_frozen_input_hashes` reads only that directory, so the
plus-or-minus-0 N=5 measured at `b7cb9a99` describes the merged tree. That is stronger than
"untouched by master's 86 commits", which is a statement about a commit range rather than
about the tree the gate actually reads.

### Residual recount on the merged tree, and a correction for BEAAA-23947

Counted independently, not inherited: **25** JSON with `"cto_cosign_ref": "BEAAA-14867"` and
**21** markdown with a co-sign line — **46 total**, reproducing the handover. Exactly **3**
files carry a live `"issue": "BEAAA-17963"` key
(`artifacts/beaaa-21704/n5/2026-08-10-8562e9a5…-vs-HEAD.json`,
`artifacts/beaaa-21704/n5/calibration_delta.json`,
`tools/calibration-deltas/2026-07-27-beaaa18123-partner-kit-n5.json`); every other hit is
`correction_note` prose, so a bare `BEAAA-17963` grep is not a safe selector.

The handover's "22 files whose only mention is `approval_provenance[].decision_ref`" is
inverted. Measured:

| Set | Count | Sweep action |
|---|---|---|
| Cite `BEAAA-14867` in a `decision_ref` | 46 | 45 JSON plus `scripts/release_gate_n5.py` |
| **Also carry the false `cto_cosign_ref`** | **22** | Edit one field, preserve another, same file |
| Cite it **only** in `decision_ref` | **24** | Do not open at all |

22 is the overlap set; the only-mention set is 24. Taken as written, a sweep owner would
build a 22-file exclusion list, then edit 22 files that list was meant to protect, while 24
hands-off files never appear on it.

Safe selector: the two uses are distinguishable by exact value. `decision_ref` is always
`BEAAA-14867#comment-<uuid>` on this tree — the bare `"decision_ref": "BEAAA-14867"` occurs
zero times — while the false co-sign is always the bare `"cto_cosign_ref": "BEAAA-14867"`.
Sweeping the exact key-and-value pair cannot catch a true citation even inside the 22
overlap files; a bare-id replace would touch 46 and corrupt 24.

Reported on BEAAA-23845 (BEAAA-23947 returns 403 from the QA seat) for carry-across to the
sweep ticket.
