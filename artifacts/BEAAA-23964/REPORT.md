# BEAAA-23964 — QA gate report (R1)

Candidate: `f589c87b6a318dc3d884542e07c29cd0a170c4ae` (branch `agent/beaaa-23953-focus-claim-escape`)
Base: `40abb5cb821718641f3322b0eb656c50f462e46b`
Code commit: `3d572d7925404e10db8016eeb752564edbdd266c`
Verdict: **CHANGES REQUESTED** — 1 blocker (F1), 2 non-blocking notes.

## Candidate integrity

Fetched from `CorwinFoundation/are-scanner`; all three commits resolve. History is linear:
`40abb5cb` -> `3d572d79` (code) -> `f589c87b` (evidence). `40abb5cb` confirmed an ancestor of `3d572d79`.

The "evidence only" claim in the description holds. `git diff --name-status 3d572d79 f589c87b` returns
exactly three added paths, all under `artifacts/beaaa-23953/`, and the tree outside `artifacts/` is
byte-identical between the two commits.

## Acceptance criteria

| # | Criterion | Result |
|---|---|---|
| 1 | 34 tests in `test_release_gate_n5_beaaa10187.py`, base had 29 | PASS — 34 passed on candidate, 29 on base |
| 2 | `test_contract_pin_reconciliation_beaaa18467.py` still passes | PASS — 2 passed |
| 3 | Rejects `"`, `\`, newline; message names the character | PASS — plus tab, CR, DEL also rejected |
| 4 | Round-trip of an accepted claim survives `yaml.safe_load` | PASS — verified through the real renderer |
| 5 | Guard is load-bearing under mutation | PASS in substance, prediction wrong (N2) |
| 6 | `grep -rn "artifact-metadata-schema" scripts/ tests/` returns nothing | FAIL as written, substance holds (N1) |
| 7 | Supreme Law Layer 2, +/-0, byte-identical both sides | PASS — reproduced exactly |

### AC7 detail

Re-ran the gate command from the description twice with identical arguments: once from the candidate
worktree at `f589c87b`, once from a clean worktree at `40abb5cb`. Both runs exit 0 and produce:

```
1e06b2a60c18a206c68db64de9ad68479869b9efee19aba395405b6f40d5f5d4  n5/2026-08-17-40abb5cb-vs-HEAD.md
54371f8340a35e710a8a96c1db49c05cdf669038a3282f33c50d6f4b54d2e84e  n5-calibration-delta.json
```

Both match the SHAs stated in the description and the committed artifacts byte for byte
(`git diff --stat -- artifacts/beaaa-23953/` empty after the rerun).

The delta is not vacuous: 8 agents (G1-G8) in the delta JSON, all Δ 0 on total and each of the four
pillars, seed spread 0 on both sides, `approval_provenance.passed: true`, behavioural gate PASS.
The receipt records `Candidate commit/tree: self / self`, so the +/-0 was settled by regenerating and
diffing bytes across two genuinely different trees rather than by trusting that field.

## F1 — BLOCKER: the control-character test misses the whole C1 range

`scripts/release_gate_n5.py:597`

```python
if any(ord(char) < 0x20 or ord(char) == 0x7F for char in focus_claim):
```

This covers C0 and DEL. It does not cover C1, `U+0080`-`U+009F`. All 32 C1 code points pass the
guard, and PyYAML does not accept them in a double-quoted scalar:

- **31 of 32** (`U+0080`-`U+0084`, `U+0086`-`U+009F`) produce a receipt whose frontmatter cannot be
  parsed at all — `yaml.safe_load` raises `ReaderError`. This is the loud-breakage mode the guard
  was added to prevent for `"`.
- **`U+0085` (NEL)** parses cleanly but silently rewrites the claim. PyYAML treats NEL as a line
  break and folds it to a space, so the receipt states a sentence the operator did not write. This
  is the silent mode the guard was added to prevent for `\`, and it is the case the guard's own
  comment describes: "a newline folds under YAML rules ... but it still rewrites the sentence the
  receipt exists to state."

Repro (`artifacts/BEAAA-23964/qa_beaaa23964_c1_sweep.py`, output in `c1-sweep-output.txt`):

```
C1 range U+0080-U+009F (32 code points), fed to the guard then the renderer:
  REJECTED by guard      : 0  []
  accepted, receipt CLEAN: 0  []
  accepted, FRONTMATTER UNPARSEABLE: 31
  accepted, claim SILENTLY MUTATED : 1
      0x85 -> 'BEAAA-1 real claim. owner: attacker'   injected keys: NONE
```

Expected: rejected with a message naming the character, as C0 controls already are.
Actual: accepted; receipt either unparseable or carrying a different sentence.

**Scope and severity, stated honestly.** No key injection occurs in any of the 32 cases — I checked
the emitted key set against the producer's own report fixture, and it is unchanged throughout. There
is no privilege escalation and no forged provenance field. `focus_claim` arrives from the operator's
own `--focus-claim` CLI argument, not from an untrusted source, so this is a robustness and
provenance-fidelity gap rather than a security hole. The realistic path in is a paste carrying a
stray C1 byte from a mis-decoded Latin-1 or word-processor source.

The change is a **strict improvement over base on every input class** — at `40abb5cb` none of this
was guarded, including `"` and `\`.

**Why it still blocks.** The render site now carries an unconditional safety assertion
(`scripts/release_gate_n5.py:880`): "Safe unescaped because `_validate_receipt_provenance` rejects
the two characters YAML reads specially inside a double-quoted scalar." Given the above that
overstates coverage, and the receipt is the durable provenance record whose entire purpose is
faithful attribution. BEAAA-23947 is queued to rewrite 46 receipt headers on the back of this guard;
closing the gap before that sweep is cheaper than after it.

For reference, PyYAML's own printable set (`Reader.NON_PRINTABLE`) admits `\x09\x0A\x0D`,
`\x20-\x7E`, `\x85`, `\xA0-퟿`, `-�`, `\U00010000-\U0010FFFF`. The fix is the CTO's to
choose; I am not proposing an implementation.

## N1 — non-blocking: AC6 does not hold as literally written

`grep -rn "artifact-metadata-schema" scripts/ tests/` returns one hit, not nothing:

```
scripts/release_gate_n5.py:542:# "under canon/artifact-metadata-schema.md". That path resolves nowhere from
```

The string survives inside the new comment that explains why the old citation was wrong. The
substance of F2 holds: the `ticket_refs` fail-closed message no longer cites the unreachable path,
and the replacement names `BEAAA-23845`, which an operator can reach.

I verified every factual claim in that new comment rather than taking it on trust:

- `canon/artifact-metadata-schema.md` does not resolve from the repo — there is no `canon/` directory. Confirmed.
- The document exists in the seat instruction directories (found in 17 of 24 seats; absent from my own QA seat, which is worth a separate look but is not this ticket).
- It names `source_refs` and contains no occurrence of `ticket_refs`. Confirmed.
- `ticket_refs` is carried by the frontmatter of `contracts/are-scanner-api-v1.md` (line 9). Confirmed.

## N2 — non-blocking: AC5's prediction is wrong, and one new test is not load-bearing

AC5 says reverting the `offenders` block makes "the two new tests" fail. It does not. With the block
removed and nothing else touched:

```
FAILED test_receipt_provenance_fails_closed[kwargs8-cannot contain a double quote]
FAILED test_receipt_provenance_fails_closed[kwargs9-cannot contain a backslash]
FAILED test_receipt_provenance_fails_closed[kwargs10-cannot contain a control character]
3 failed, 31 passed
```

The two separately named new tests both **pass** under the mutant (`2 passed, 32 deselected`). The
guard is load-bearing, so AC5's substance holds via the three parameterised cases.

Worth flagging for the record: `test_an_unrejected_double_quote_would_break_the_frontmatter` cannot
detect removal of the guard at all. It edits already-rendered markdown text and never calls
`_validate_receipt_provenance`. It documents why the guard matters; it does not prove the guard
exists. That is a reasonable thing for a test to do, but it should not be counted as guard coverage.

## Regression

- All three importers of `release_gate_n5` run clean on the candidate. The description named two; the
  third, `tests/test_warranty_recipient_integrity_beaaa12031.py`, passes 16/16 on both candidate and base.
- No CI node-id pins reference `test_release_gate_n5_beaaa10187` across `.github/ scripts/ ops/ tools/ bin/`,
  and no test function was renamed in this change, so the BEAAA-23914 rename hazard does not apply.
- The N=5 gate itself runs green end to end from both sides.

## Evidence in this directory

- `qa_beaaa23964_guard_probe.py` / `guard-probe-output.txt` — AC3, AC4 and the first adversarial pass
- `qa_beaaa23964_c1_sweep.py` / `c1-sweep-output.txt` — the F1 repro across all 32 C1 code points
