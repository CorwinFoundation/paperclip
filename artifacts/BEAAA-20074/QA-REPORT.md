# BEAAA-20074 — QA gate report: synthetic voice E2E and no-content invariant

**Verdict: CHANGES REQUESTED**
Date: 2026-08-17
Gate issue: BEAAA-20074 (`21359313-3636-43d5-b10f-9b7b532c012e`)
Implementation parent: BEAAA-19572
Construction task: `5de6b1ab-bc54-4a1d-8cf2-9a2cdc594bf9` — Scanner Engineer #3 (`a48f9b14-e8ef-4bfd-9297-61d179b2cf10`)

## Candidate under test

No candidate SHA was ever proposed on the superseding construction task (`5de6b1ab`, zero comments). The only
material that exists for this scope is:

| field | value |
|---|---|
| branch | `origin/agent/beaaa-20073-voice-e2e` |
| head | `bd7ebabbcc1509b0043b961318fbef46fbebb91c` (2026-08-01 06:56:27 +0000) |
| merge-base with master | `79ac65f34e2106d25587c08c3a99bea3b60bc88a` (2026-07-31) |
| master head at test time | `ae14923083d3b002de3926b8eff6fabb26038c89` (2026-08-16) |

Tested in a detached worktree at `bd7ebabb`. Nothing from this scope is on `master`.

## What I ran

1. `python3 tools/post-bind-ingest/scripts/run_synthetic_voice_e2e.py --output-dir …` → exit 0,
   `status: PASS`, 9/9 assertions true (`baseline-report.json`).
2. Content-boundary probe: prohibited-*equivalent* voice fields nested inside an allowed key
   (`probe_nocontent.py` / `.log`).
3. Full-pipeline poisoned run through the shipped `run()` entry point (`probe_full.py` / `.log`,
   `poisoned-report.json`, `poisoned-batches.json`).
4. Mutation testing of the shipped assertions, Python (`mutants.py` / `.log`) and Go
   (`prohibitedPath` neutered in place, `go test ./internal/source/ -run SyntheticVoice`).
5. CI reachability audit of `.github/workflows/` at the candidate.

Synthetic fixtures only. No platform contact, no deploy, no real or sample call data. VAPI hold untouched.
Synthetic SSN/PAN/phone tokens used in the probes are redacted in these artifacts.

## Defects

### D1 — CRITICAL. The no-content invariant does not hold. Prohibited call content crosses the Collector boundary and the harness still reports PASS.

Both matchers are exact-name sets: Go `prohibitedVoiceContentKeys`
(`tools/are-collector/internal/source/synthetic_voice.go:12`) and Python `PROHIBITED`
(`tools/post-bind-ingest/scripts/run_synthetic_voice_e2e.py:24`). Neither does prefix, substring, or value
matching. The Go allowlist `allowedSyntheticVoiceKeys` only guards depth 0, and `Normalize` copies
`producer_pins` verbatim into `NormalizedEvent.Attributes` (`synthetic_voice.go:95`).

Repro:

```
python3 artifacts/BEAAA-20074/probe_full.py      # from a worktree at bd7ebabb
```

The record's `producer_pins` carries `transcript_url`, `recording_uri`, `caller_phone_number`, and
`speech_text` holding a synthetic SSN and card number.

Expected: rejected at the Collector schema boundary, or caught by the recursive scanner.
Actual: accepted, signed into all three batches, normalized, and written to `batches.json`. Report:

```
REPORT STATUS: PASS
"no_content_recursive": true
transcript_url         present=True
recording_uri          present=True
caller_phone_number    present=True
speech_text            present=True
```

The gate's headline requirement — "no audio, recording, transcript, spoken PII, call content, **or
prohibited equivalent** appears in any emitted batch" — fails. Severity is critical because this is the
single invariant the whole voice track is gated on.

### D2 — CRITICAL. `no_content_recursive` is a hardcoded literal, not a measurement.

`run_synthetic_voice_e2e.py:75` sets `"no_content_recursive": True` unconditionally.

Mutant M2 (`inspect_no_content` replaced with a no-op) — **survives**:

```
M2 inspect_no_content -> no-op        : PASS | no_content_recursive= True
```

The entire content scanner can be deleted and the reported assertion stays green.

### D3 — CRITICAL. `TestSyntheticVoiceRejectsContentRecursively` does not test recursion.

`synthetic_voice_test.go:5` plants prohibited keys under a top-level `nested` key. `nested` is not in
`allowedSyntheticVoiceKeys`, so the record is rejected by the allowlist whether or not `prohibitedPath` runs.
No case nests content under an *allowed* key — which is exactly the D1 hole.

Mutant M1 (`prohibitedPath` returns `"", false` immediately) — **survives**:

```
=== MUTANT M1 (prohibitedPath always false) go test ===
--- PASS: TestSyntheticVoiceRejectsContentRecursively (0.00s)
--- PASS: TestSyntheticVoiceRejectsUnknownFields (0.00s)
```

Control mutant M4 (hash chain broken) is caught (`FAIL`), so the mutation rig has teeth.

### D4 — HIGH. The no-content invariant is not required CI. It is not CI at all.

`grep -rn "synthetic_voice\|voice_e2e\|no.content" .github/workflows/` at `bd7ebabb` returns nothing.
The pytest jobs in `tests.yml` and `pr-unit-tests.yml` collect `tests`, `tools/portal_v1`,
`tools/are-connector/tests`, and `engineering/probe-sdk/tests`. `tools/post-bind-ingest/tests/` is in none of
them, so `test_synthetic_voice_e2e.py` never executes in CI. The gate requires proving the invariant is
required CI; the requirement is unmet.

`go test ./...` in `are-collector-unit-tests` does reach `synthetic_voice_test.go`, but see D3 — that test
does not test what it is named for.

### D5 — HIGH. The Log Tracker leg is not exercised. `log_tracker_interface` measures unrelated state.

`run_synthetic_voice_e2e.py:70-71` calls `sink.seed_scanner_run(scan_id="voice-synthetic-bind", …)` on a
fresh SQLite file and then counts rows in `scanner_runs`. The normalized voice events are never written to
Log Tracker. The assertion is "a row I just inserted exists".

Mutant M3 (normalization yields zero events) — **survives**:

```
M3 zero normalized events            : PASS | log_tracker_interface= True | continuity= True
```

The acceptance criterion "drives signed sequential contract-v1.0 batches through … Log Tracker interfaces"
is not demonstrated.

### D6 — MEDIUM. `continuity` is an alias for `prev_batch_hash_chain`, reported as a separate check.

`run_synthetic_voice_e2e.py:75`: `"continuity": chain` — the identical expression already reported as
`prev_batch_hash_chain`. Two named assertions, one measurement. Continuity state transitions and gap
detection are never exercised. Confirmed by M3 above: `continuity` stays true with zero events in flight.

### D7 — MEDIUM. Candidate is ~2 weeks behind master and has no proposed SHA.

Merge-base `79ac65f3` (07-31) against master head `ae149230` (08-16). Results measured here do not bind to
what would ship. Separately, the superseding construction task `5de6b1ab` has zero comments and named no
candidate, and is itself recorded as blocked by this QA gate — a dependency cycle in which nothing can land.

## What passed

The signing, chaining, replay, and tamper legs have real teeth. Control mutant M4 (chain broken) is caught
and drives the report to `FAIL`. `prev_batch_hash` chaining, Ed25519 JWT verification via keyring, the
`ManifestIdempotencyStore` replay rejection, and `batch_sha256` tamper rejection all behave as claimed.
Collector core is untouched: the diff against the merge-base adds three new files under `tools/are-collector`
and modifies nothing existing there, nor in `tools/post-bind-ingest/src` or `tools/log-tracker/src`.

## Required to clear the gate

1. Replace exact-name matching with a matcher that catches prohibited *equivalents* (prefix/substring over
   the prohibited stems, applied at every depth), and apply the metadata-only allowlist recursively rather
   than at depth 0 only. `producer_pins` must be a closed value schema, not a free-form map.
2. Make `no_content_recursive` reflect a measurement.
3. Add nested-under-an-allowed-key cases to the Go test so M1 dies.
4. Wire the E2E test into required CI on a path that actually collects it.
5. Drive normalized voice events into Log Tracker and assert on those rows, or drop the claim.
6. Give `continuity` an independent measurement or drop it from the report.
7. Rebase onto current master and name an exact candidate SHA on `5de6b1ab`.

Re-test on the next candidate reuses this gate issue and records `SUPERSEDED: bd7ebabb -> <new>`.
