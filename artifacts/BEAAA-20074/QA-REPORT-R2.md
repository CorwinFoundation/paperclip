# BEAAA-20074 — QA gate R2, candidate `b64ee660`

**Verdict: CHANGES REQUESTED.** Six of the seven R1 items are closed with mutation proof.
Item 1 — the one the CEO named as deciding this gate — is closed against the *key* vector and
still open against the *value* vector.

| field | value |
| --- | --- |
| Candidate | `b64ee66033bd871c428a1626cf038f642a0657f4` |
| Subject | `BEAAA-20073 close synthetic voice no-content gaps` (authored 2026-08-17T04:27:21Z) |
| Branch tip | confirmed by authenticated `git ls-remote` against `CorwinFoundation/are-scanner` |
| Master base | `2b8afb8c` (a real ancestor of master; master tip `fb2cf1ef` is 5 commits ahead) |
| Superseded | `bd7ebabb` → `b64ee660` |
| Worktree | detached at `b64ee660`, `/tmp/qa20074-b64ee660` |

## Item-by-item

| # | Item | Result | Proof |
| --- | --- | --- | --- |
| 1 | Stem match at every depth, recursive allowlist, closed `producer_pins` | **OPEN** | key vector closed; value vector leaks (below) |
| 2 | `no_content_recursive` reports a measurement | CLOSED | M1 scanner→no-op: harness dies `TypeError`, red |
| 3 | Nested-under-allowed-key case kills a `prohibitedPath` mutant | CLOSED | M2 stub: `TestProhibitedPathMatchesNestedEquivalentUnderAllowedKey` FAILS |
| 4 | Invariant on a CI path that collects it | CLOSED | `passive-monitor-demo.yml` PR-on-master collects `test_synthetic_voice_e2e.py`; `pr-unit-tests.yml` runs `go test ./...` on every PR with no path filter |
| 5 | Log Tracker driven by normalized events | CLOSED | M5 zero rows → `log_tracker_determinations_persisted=False` |
| 6 | `continuity` independently measured | CLOSED | M6 starved → `continuity_gate_healthy=False` while `prev_batch_hash_chain=True` |
| 7 | Rebased, exact SHA named | CLOSED | base `2b8afb8c` is on master; SHA named by CEO and verified |

## R1 regression — the original D1 repro is dead

My unmodified R1 probe (`probe_full.py`, sha256 `93a6c9e6…`, restored from evidence commit
`f8a3467b`) now fails closed at the Collector boundary:

```
ValueError: prohibited voice content field: producer_pins.transcript_url
```

All three R1 critical defects are genuinely fixed. Mutants M1, M2, M3, M5, M6 and control M7 were
all killed. Collector core is untouched — the whole branch is additions-only (458 insertions, 0
deletions; three new files under `tools/are-collector/`, none modified).

## The open item — prohibited content rides in as a *value*

The fix matches prohibited stems against **keys** at every depth, and never against **values**.
Every allowed scalar is constrained only by a character-class format check, not by content.

- `synthetic_voice.go:165-166` — stems matched against `lowerKey` only.
- `synthetic_voice.go:33,117` — `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$` on `event_id`, `session_id`,
  `agent_id`, `deployment_id`, `outcome_status`. Digits, dots, colons and hyphens are legal.
- `run_synthetic_voice_e2e.py:50` — Python scanner also matches stems on keys only.
- `run_synthetic_voice_e2e.py:23,56` — the only value-level check is two patterns, SSN and card.
  There is no phone, email, URI or transcript-reference pattern.

Measured at the real Go boundary, `session_id` only:

| value | Go boundary | end-to-end |
| --- | --- | --- |
| `555-010-9999` (caller phone) | accepted | **PASS, in signed batches** |
| `14155550123` (E.164) | accepted | **PASS, in signed batches** |
| `cdn.example.com:transcript.abc123` | accepted | **PASS, in signed batches** |
| `caller.jane.doe-at-example.com` | accepted | **PASS, in signed batches** |
| `123-45-6789` (SSN) | accepted | blocked by the Python value pattern |
| `4111-1111-1111-1111` (PAN) | accepted | blocked by the Python value pattern |

End-to-end with `session_id=555-010-9999` and `agent_id=14155550123`:

```
REPORT STATUS: PASS
no_content_recursive : True
nodes_scanned/violations: 331 / 0
caller phone present in signed batches.json = True
e164 present in signed batches.json         = True
```

The number is signed into all three batches (`voice-batch-1..3`). Persisted Log Tracker rows do
**not** contain it — those fields are hashed on the way in — so this is an emitted-batch leak, not
a persisted-record leak. The gate covers both, so the invariant still does not hold.

Two of the four content classes from my R1 poison set — a caller phone number and a transcript
reference — still reach signed batches. They have only moved from a nested `producer_pins` key to
an allowed scalar value.

## `TestSyntheticVoiceRejectsContentInjectedIntoAllowedScalar` passes for the wrong reason

`synthetic_voice_test.go:43-52` targets exactly this vector, and it passes — but all three of its
values are rejected by the character class, not by any content check:

```
'https://example.invalid/transcript'  -> synthetic voice field session_id has invalid format
'synthetic SSN 123-45-6789'           -> synthetic voice field session_id has invalid format
'4111 1111 1111 1111'                 -> synthetic voice field session_id has invalid format
```

Each contains a space or a slash. Remove those and the same PII is accepted. The test asserts the
charset, so it will keep passing no matter what happens to content detection.

## To close item 1

Apply content detection to **values** as well as keys, at the redaction boundary in
`synthetic_voice.go` rather than only in the harness assertion, and add at least one case whose
value is charset-legal so the test cannot pass on the format check alone. If identity fields are
meant to be opaque, an explicit opaque-token rule (for example a required non-PII prefix, or a
hash) is stronger than a charset and cheaper to prove.

## Reproduce

```
git worktree add --detach /tmp/qa20074-b64ee660 b64ee660
cd /tmp/qa20074-b64ee660
python3 artifacts/BEAAA-20074/probe_scalar_pii.py    # Go-boundary acceptance table
python3 artifacts/BEAAA-20074/probe_phone_e2e.py     # end-to-end leak into signed batches
```

## Separate finding — not this candidate's fault

Merge `2b8afb8c` silently dropped all ten of my R1 evidence files under `artifacts/BEAAA-20074/`.
One parent (`dec05b7e`) carried them, the other (`2901e3b1`) did not, and the merge kept none, so
they are absent from current master `fb2cf1ef`. Nothing on the candidate branch caused this. Owner:
whoever owns master merge hygiene. The files remain retrievable from `f8a3467b`.

## Holds

Synthetic fixtures only. No platform access, no VAPI contact, no live or sample real call data.
No merge, shared-environment or production promotion. VAPI hold unchanged.
