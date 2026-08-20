# BEAAA-23865 — QA gate: TAU scope guard pinned candidate

**Verdict: APPROVED**
**Candidate under test:** `64bca4d00d7169dfcae4f32868a3501eca4e6f38` (branch `agent/beaaa-23863-tau-scope-guard`, tip)
**Parent:** BEAAA-23863 · **Prior QA-approved base:** `a765ba9c0570a0ceba5fa0547f655ce080132bb9` (BEAAA-21432 R4)
**Tested:** 2026-08-17 · QA Engineer

The candidate is one commit on top of the R4-approved `a765ba9c`, touching one file
(`tests/test_provisional_tau_support_crm_beaaa20725.py`, +43/-2). It replaces the
`git log -1 -- <feature paths>` candidate resolution in
`test_complete_tau_feature_diff_is_confined_to_authorized_paths` with an immutable pinned SHA,
and adds `test_pinned_feature_range_ignores_later_mixed_commit`.

Test file blob at the candidate: `sha256 8876fb69c6bfcf0a535b537d00048e088fe59801855515224173126b8706cfd3`.

## Acceptance criteria

### 1. Filtered suite passes — PASS

Clean worktree at the candidate (`git worktree add --detach /tmp/qa23865-acc 64bca4d0`):

```
$ python3 -m pytest -q tests/test_provisional_tau_support_crm_beaaa20725.py \
    -k 'scope_guard or complete_tau or pinned_feature'
collected 7 items / 4 deselected / 3 selected
3 passed, 4 deselected in 0.72s        EXIT=0
```

The three selected tests are `test_complete_tau_feature_diff_is_confined_to_authorized_paths`,
`test_scope_guard_preserves_candidate_provenance_before_and_after_merge`,
`test_pinned_feature_range_ignores_later_mixed_commit`.

### 2. Guard compares immutable base to immutable candidate — PASS

`TAU_FEATURE_BASE = a913131492a3c39ccf097eeab8a8602a49f24711` and
`TAU_FEATURE_CANDIDATE = a765ba9c0570a0ceba5fa0547f655ce080132bb9`, both passed
directly to `_assert_candidate_scope`. No resolution step remains in that test.

Both anchors verified independently rather than read off the source:

| check | result |
|---|---|
| `git cat-file -t a9131314` | `commit` |
| `a9131314` ancestor of candidate | yes |
| `a9131314` on `master` | yes |
| `a765ba9c` ancestor of candidate | yes (direct parent) |
| `a765ba9c` on `master` | no — candidate not promoted |

`git diff --name-only a9131314...a765ba9c` returns **exactly the 15 declared
`TAU_FEATURE_PATHS`** — 0 missing, 0 extra. The pin is not merely well-formed; it resolves to
the real feature diff.

### 3. Regression advances a temp repo with a mixed commit and still validates the pinned range — PASS

`test_pinned_feature_range_ignores_later_mixed_commit` builds a temp repo, commits a TAU-path
candidate, then commits a **mixed** change (`evals/tau.py` + `notes.md`). It asserts the
resolver drifts (`_feature_candidate(...) != pinned_candidate`) and that the pinned range still
validates. Both halves are load-bearing — see mutants M4 and M5.

### 4. Forbidden-prefix changes outside the allowlist remain rejected — PASS

`test_scope_guard_preserves_candidate_provenance_before_and_after_merge` still ends in
`pytest.raises(AssertionError)` against a commit adding `api/routes.py`. Proven non-vacuous by
mutant M6.

## The fix works — A/B measurement of the reported defect

BEAAA-23863's repro (filed from BEAAA-21432 R4): a later commit touching a TAU file plus
anything outside the set false-fails the guard. I applied the **identical** commit
(`tools/active_test_registry.py` + `docs/adr/ADR-9999-qa-repro.md`) to a worktree at the pre-fix
base and one at the candidate:

| build | result |
|---|---|
| pre-fix `a765ba9c` | **FAILS** — `AssertionError: assert not {'docs/adr/ADR-9999-qa-repro.md'}` at line 70 |
| candidate `64bca4d0` | **PASSES** — 1 passed, 6 deselected |

The defect reproduces before the change and is gone after it.

## Mutation battery — 6 mutants, 6 killed

Run in a second worktree (`/tmp/qa23865-mut`) so the acceptance run was never contaminated.
Control (unmutated) = 3 passed. Worktree restored to the exact candidate blob after each.

| # | mutation | expected | result |
|---|---|---|---|
| M1 | pin → commit adding `api/qa_injected_forbidden.py` | fail | **killed** |
| M2 | pin → commit adding `docs/qa_injected_note.md` (outside allowlist, not a forbidden prefix) | fail | **killed** |
| M3 | drop `tools/probe_registry.py` from `TAU_FEATURE_PATHS` | fail | **killed** |
| M4 | new test uses the drifted resolver instead of the pin | fail | **killed** |
| M5 | later commit unrelated-only (no TAU file) → drift precondition gone | fail | **killed** |
| M6 | `FORBIDDEN_PREFIXES = ()` | fail | **killed** |

M1/M2 confirm the pinned guard still polices both the forbidden-prefix rule and
allowlist-exactness. M3 confirms the allowlist is exact in both directions. M4/M5 confirm the
new regression is about the pin and about the commit genuinely being mixed. M6 confirms
criterion 4's rejection is real.

## Regression

- **Blast radius is nil by identity.** `git diff --name-status --find-renames a765ba9c 64bca4d0`
  reports one `M` entry. `git ls-tree -r` over both SHAs excluding that one file hashes
  identically (`3349c961cc525617bea597c986141eab1621f604f4477dc7582d83c743858b30`).
  `--diff-filter=D` = 0. Every surface QA approved at R4 is byte-identical here.
- **No coverage lost.** Collected test-id set diff, approved base vs candidate: zero lost,
  one added (`test_pinned_feature_range_ignores_later_mixed_commit`). 6 passed at `a765ba9c` →
  7 passed at the candidate.
- **TAU acceptance set green at the candidate:** the 6 TAU test files in `TAU_FEATURE_PATHS` —
  **40 passed in 73.29s**.
- No production code references `_feature_candidate`, `TAU_FEATURE_BASE`,
  `TAU_FEATURE_CANDIDATE` or `TAU_FEATURE_PATHS`; the only other hits are a stale copy of the
  same test file in the producer's `/tmp` worktree. Test-only change.
- `_feature_candidate` is still used by two tests — not dead code.

## Technical layer

- Fail-closed confirmed: pointing `TAU_FEATURE_CANDIDATE` at an unreachable SHA raises
  `subprocess.CalledProcessError ... exit status 128`. It errors loudly rather than passing
  silently.
- No secrets or PII. Temp-repo commits use `scanner-test@backbond.invalid`.
- All git subprocesses pass `cwd=repo`.
- Promotion status: `git merge-base --is-ancestor 64bca4d0 master` is false. Not on master, so
  no governance breach.

## Non-blocking observations for the CTO

1. **The guard is now a frozen historical assertion.** With both endpoints pinned it computes a
   constant forever and cannot police future changes. That is the intended semantics of
   BEAAA-23863 and is why this is not a blocker, but the ongoing-policing role now rests
   entirely on `test_scope_guard_preserves_candidate_provenance_before_and_after_merge`.
2. **Squash-merge or GC would break it with a confusing error.** If `a765ba9c` ever becomes
   unreachable the test dies with `CalledProcessError ... exit status 128` rather than a
   readable assertion. `master` carries 170 merge commits and no squash convention, so this is
   unlikely; a `git cat-file -e` precondition with a clear message would remove the sharp edge.

Neither affects the four acceptance criteria.

## Reproduction

```bash
git worktree add --detach /tmp/qa23865-acc 64bca4d00d7169dfcae4f32868a3501eca4e6f38
cd /tmp/qa23865-acc
python3 -m pytest -q tests/test_provisional_tau_support_crm_beaaa20725.py \
  -k 'scope_guard or complete_tau or pinned_feature'
git diff --name-only a913131492a3c39ccf097eeab8a8602a49f24711...a765ba9c0570a0ceba5fa0547f655ce080132bb9
```
