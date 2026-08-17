# BEAAA-20074 — QA gate R4, candidate `89932217`

**Verdict: APPROVED, scoped.** `SUPERSEDED: 0cd9157a -> 89932217`.

This round exists because the candidate was rebased onto a new `origin/master`, not because
the work changed. The BEAAA-20073 delta is byte-identical to the tree approved in R3. What is
new is the base: `origin/master` now carries the BEAAA-20704 synthetic voice *metadata* driver,
which lands a second voice driver in the same Go package. That interaction is what R4 tested.

| field | value |
| --- | --- |
| Candidate | `89932217ebfadd2d0b6dbe4c6f233a09a61500b4` |
| Subject | `BEAAA-20073 reject content in voice identity values` (authored 2026-08-17T06:36:10Z) |
| Branch | `agent/beaaa-20073-voice-e2e`, confirmed current tip by authenticated `git ls-remote` |
| Master base | `7f8be7a113d2418d4370117a430802303e43567b`, confirmed an ancestor of the candidate |
| Superseded | `0cd9157a65501c99cac4ddb892cfec35d3f96b42` (R3, approved) |
| Worktree | detached at `89932217`, `/tmp/qa20074-r4` |
| Shape | 7 files added, 1 CI line modified, 489 insertions, 0 deletions |

## Provenance — the lineage repair is a base change, not a work change

Authenticated read against `CorwinFoundation/are-scanner` (inline PAT, not a remote-tracking
ref — the local `origin/master` was stale at `fb2cf1ef` and the local branch ref was stale at
`bd7ebabb`):

```
89932217ebfadd2d0b6dbe4c6f233a09a61500b4  refs/heads/agent/beaaa-20073-voice-e2e
7f8be7a113d2418d4370117a430802303e43567b  refs/heads/master
```

Tree comparison against the R3-approved candidate:

```
$ git diff --name-status 0cd9157a 89932217
A  tools/are-collector/internal/collector/synthetic_voice_metadata_pipeline_test.go
A  tools/are-collector/internal/source/synthetic_voice_metadata.go
A  tools/are-collector/internal/source/synthetic_voice_metadata_test.go
```

All three added files are byte-identical to `7f8be7a1`, and all three arrived from
`6c14ad96` — the BEAAA-20704 driver this seat approved separately on 2026-08-17. Every one of
the eight files in the BEAAA-20073 delta is byte-identical to `0cd9157a`. So the candidate
tree is exactly the R3-approved tree plus a separately approved driver.

Collector core is untouched: no file under `internal/collector`, `internal/egress`,
`internal/schemav1`, `internal/redaction`, `internal/adapter`, or `internal/triggers` appears
in the delta. There are no deletions anywhere in the four commits.

The working copy shows three `tools/partner-kit/` files as modified on checkout. That is the
repo-wide CRLF filter, not candidate content: `git write-tree` in the worktree returns
`e6eb04411b5bbc775b68297541c95c61d30442fe`, identical to the candidate's own tree hash.

## Item 1 — content rejection at the Go boundary, measured first-hand

Run against the candidate's own `cmd/synthetic-voice-source` binary, one value per run,
everything else held at the benign fixture:

| `session_id` value | rejection |
| --- | --- |
| `555-010-9999` | `prohibited voice content value: session_id` |
| `14155550123` | `prohibited voice content value: session_id` |
| `cdn.example.com:transcript.abc123` | `prohibited voice content value: session_id` |
| `caller.jane.doe-at-example.com` | `prohibited voice content value: session_id` |
| `session-5550109999` | `prohibited voice content value: session_id` |
| `123-45-6789` | `synthetic voice identity fields must use opaque fixture tokens` |
| `session-synthetic` (control) | accepted, emits a content-free normalized event |

The five content values are rejected by `prohibitedValue`, with the content-specific reason
rather than a charset or format reason. The hyphenated SSN is caught one layer out by the
opaque-token constraint, as in R3.

## Acceptance results

| check | result |
| --- | --- |
| Go suite, all packages | 44/44 pass, 0 failures (23 were the R3 set; 21 arrive with the new base) |
| Python E2E | `PASS`, 9/9 assertions true, 331 nodes scanned, 0 violations, 3 batches / 3 rows / 3 events |
| Regression, the CI workflow's exact pytest list | 85 passed |
| No-content invariant in required CI | real — the candidate adds `test_synthetic_voice_e2e.py` to `passive-monitor-demo.yml` |
| Collector core untouched | confirmed by delta inspection |

E2E counts are identical to R3, which is the expected result for a byte-identical delta.

## Mutation battery — 10 mutants, all killed at their proving layer

Go source mutants, candidate's own tests as oracle:

| mutant | result | catcher |
| --- | --- | --- |
| M2 `prohibitedPath` → stub | KILLED | `TestProhibitedPathMatchesNestedEquivalentUnderAllowedKey` |
| M3 `prohibitedValue` → stub | KILLED | `TestSyntheticVoiceRejectsContentInjectedIntoAllowedScalar` (wrong error message) |
| M7 `validateProducerPins` → `nil` | survives unit level, **KILLED at E2E** | E2E `status=FAIL`, `content_injection_rejected=false` |
| M8 drop the phone regex only | KILLED | `TestSyntheticVoiceRejectsContentInjectedIntoAllowedScalar` |
| M9 stem match substring → exact | KILLED | `TestSyntheticVoiceRejectsContentInjectedIntoAllowedScalar` |

M8 and M9 are new in R4. They relax the guard's strictness instead of removing it, which is
the mutation class that catches a sloppily-widened regex. M9 reintroduces the exact R2 defect
and the suite reddens, so the R2 regression is genuinely pinned.

Python harness mutants:

| mutant | result | observation |
| --- | --- | --- |
| M1 `inspect_no_content` → no-op | KILLED | `EXC:TypeError` |
| M1b scanner → fabricated clean count | KILLED | `EXC:KeyError` |
| M4 hash chain broken (control) | KILLED | `prev_batch_hash_chain=false` |
| M5 Log Tracker zero rows | KILLED | `log_tracker_determinations_persisted=false` |
| M6 continuity gate → `None` | KILLED | `EXC:AttributeError` |
| M10 batch hash → constant | KILLED | `EXC:ReceiverSchemaError` |

M1b and M10 are new in R4. M1b is the harder version of M1: instead of removing the scanner it
returns a plausible `{"nodes": 331, "violations": 0}`. The harness still reddens, so the
no-content count is derived rather than asserted.

Source was restored after every mutant and verified byte-identical to the candidate.

## Finding F1 — content leak in the metadata driver on `master`. Not this candidate. Not fixed.

This is the reason the approval below is scoped, and it needs an owner.

`tools/are-collector/internal/source/synthetic_voice_metadata.go:195` validates three producer
strings for non-emptiness only:

```go
if producer.CollectorVersion == "" || producer.DriverVersion != SyntheticVoiceMetadataMappingVersion
   || producer.ScannerVersion == "" || producer.CorpusVersion == "" {
```

`Normalize` then copies `producer` verbatim into `NormalizedEvent.Attributes` (line 259).
`collector_version`, `scanner_version`, and `corpus_version` are therefore unbounded free
text on a driver whose contract is content-free.

Probe: a signed event carrying
`TRANSCRIPT: caller Jane Doe said my SSN is 123-45-6789 and my card is 4111 1111 1111 1111, recording at https://cdn.example.com/calls/rec-77.mp3, phone +1-415-555-0132`
in each of the three fields in turn, driven through `Pipeline{}.CollectDriver` and
`egress.NewSignedChainedBatch` exactly as the repo's own
`synthetic_voice_metadata_pipeline_test.go` does. Result, 3 of 3 fields:

```
ACCEPTED at driver construction (no rejection)
LEAK: content present in normalized events (1235 bytes)
LEAK IN SIGNED BATCH via producer.scanner_version:   markers 123-45-6789 / 4111 1111 1111 1111
                                                     / cdn.example.com / +1-415-555-0132 / TRANSCRIPT
LEAK IN SIGNED BATCH via producer.collector_version: same five markers
LEAK IN SIGNED BATCH via producer.corpus_version:    same five markers
```

The driver's own test `TestSyntheticVoiceMetadataRejectsFreeTextInNormalizedStringValues`
passes while this holds. It covers seven fields — `source_event_id`, `agent_id`,
`deployment_id`, `action_class`, `outcome_status`, and both determination fields — and never
touches the three producer strings, which appear in the suite only as the benign fixture
`"0.5.0"`. A guard exercised solely against a benign fixture reports the property as proven
while the driver does not enforce it. The test name claims coverage of normalized string
values; three normalized string values are unguarded.

Scope and reachability, stated plainly:

- The defect is in the base, on `master` at `7f8be7a1`. The four BEAAA-20073 commits neither
  introduce nor touch it.
- The BEAAA-20073 driver does not share the weakness. Its `validateProducerPins`
  (`synthetic_voice.go:195`) requires exact equality against a closed pin set and a matching
  key count, so it has no free-form producer channel.
- The metadata driver has zero references outside its own file and its two test files, so it
  is not registered with the collector and has no production path today. The route to a signed
  batch is nonetheless demonstrated by the repo's own pipeline test, which is what the probe
  reused.

This is the same family as the F1 residual reported on BEAAA-20704 and filed as BEAAA-23896,
but with stronger evidence: a proven leak into a signed batch rather than a weak charset guard.

## Verdict

APPROVED for candidate `89932217ebfadd2d0b6dbe4c6f233a09a61500b4` against this gate's
acceptance scope: the BEAAA-20073 synthetic voice source, its E2E, and the no-content invariant
on that path. All nine assertions hold, regression is clean, and the ten-mutant battery leaves
no survivor at its proving layer.

This approval does not assert that the no-content invariant holds repo-wide. F1 is open on
`master` and is a precondition for promoting any voice ingest path that registers the metadata
driver. Promotion, merge, and deployment remain separately gated and unauthorised here.

## Reproduce

```
git worktree add --detach /tmp/qa20074-r4 89932217ebfadd2d0b6dbe4c6f233a09a61500b4
cd /tmp/qa20074-r4
python3 tools/post-bind-ingest/scripts/run_synthetic_voice_e2e.py
(cd tools/are-collector && go test ./...)
sh   artifacts/BEAAA-20074/r4_mutants_go.sh
python3 artifacts/BEAAA-20074/r4_mutants_py.py
(cd tools/are-collector && go test ./internal/collector/ \
   -run TestQAProbeProducerVersionStringsCarryContentIntoSignedBatch -v)   # F1 probe
PYTHONPATH=tools/post-bind-ingest/src:tools/log-tracker/src python3 -m pytest -q \
  tools/log-tracker/tests/test_package_metadata.py \
  tools/post-bind-ingest/tests/test_synthetic_voice_e2e.py \
  tools/post-bind-ingest/tests/test_passive_log_reader_e2e.py \
  tools/post-bind-ingest/tests/test_lifecycle_demo.py \
  tools/post-bind-ingest/tests/test_demo_app.py \
  tools/post-bind-ingest/tests/test_receiver.py \
  tools/post-bind-ingest/tests/test_continuity_gate.py \
  tools/post-bind-ingest/tests/test_detector_input.py
```

The F1 probe and both mutation scripts are committed alongside this report. Note that the
mutation scripts edit the worktree source in place and restore it; run them only in a
throwaway worktree.
