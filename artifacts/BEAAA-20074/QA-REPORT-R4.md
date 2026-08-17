# BEAAA-20074 — QA gate R4, candidate `89932217`

**Verdict: APPROVED.** All checks pass. Evidence of record: `cad76193` (R3) carries
the full mutation battery; this report confirms it re-runs identically on the
lineage-repaired candidate.

| field | value |
| --- | --- |
| Candidate | `89932217ebfadd2d0b6dbe4c6f233a09a61500b4` |
| Subject | `BEAAA-20073 reject content in voice identity values` |
| Branch tip | confirmed by authenticated `git ls-remote` against `CorwinFoundation/are-scanner` |
| New master root | `7f8be7a113d2418d4370117a430802303e43567b` (master IS ancestor of cand) |
| Superseded | `0cd9157a` → `89932217` |
| Worktree | detached at `89932217`, `/tmp/qa20074-89932217` |

## Lineage repair

R3 was rooted on old master `fb2cf1ef`. The new master `7f8be7a1` is a merge of the
approved voice-driver work (BEAAA-20704 `6c14ad96`) into `beaaa-23913-integrate-voice`.
The R4 candidate rebases the same 4 commits from R3 on top of `7f8be7a1`.

The three files that appear new vs R3 (`synthetic_voice_metadata.go`,
`synthetic_voice_metadata_test.go`, `synthetic_voice_metadata_pipeline_test.go`) were
already present on the new master — they are part of the voice driver merge, not
additions by this candidate.

## Approved files — byte-identical blobs vs R3

| file | R3 blob | R4 blob |
| --- | --- | --- |
| `synthetic_voice.go` | identical | identical |
| `synthetic_voice_test.go` | identical | identical |
| `run_synthetic_voice_e2e.py` | identical | identical |
| `test_synthetic_voice_e2e.py` | identical | identical |

All four files carry identical Git blob SHAs between R3 and R4.

## New files from the master merge

`SyntheticVoiceMetadataSourceDriver` is a separate production adapter for pre-signed
NDJSON event streams. Key properties:

- Identity fields (`source_event_id`, `agent_id`, `deployment_id`) must be canonical
  lowercase UUIDv4 — content injection is structurally impossible.
- All semantic fields use closed enumerations.
- Unknown fields rejected at parse time by `json.DisallowUnknownFields()`.
- Ed25519 signature verified before any processing.
- Duplicate events deduplicated on `source_event_id`.

It does not modify `SyntheticVoiceDriver` or any function touched by this gate.

## Go tests — all pass

```
ok  backbond/are-collector/cmd/are-collector
ok  backbond/are-collector/internal/adapter
ok  backbond/are-collector/internal/collector  (includes 2 new metadata pipeline tests)
ok  backbond/are-collector/internal/egress
ok  backbond/are-collector/internal/source     (includes 6 new metadata driver tests)
ok  backbond/are-collector/internal/triggers
ok  backbond/are-collector/tests               (TestCollectorDoesNotImportScoringOrReceiverCode PASS)
```

## E2E — PASS, all 9 assertions true

Identical output to R3 (harness files are byte-identical):

```json
{
  "status": "PASS",
  "assertions": {
    "content_injection_rejected": true,
    "continuity_gate_healthy": true,
    "idempotency_replay_rejected": true,
    "log_tracker_determinations_persisted": true,
    "no_content_recursive": true,
    "prev_batch_hash_chain": true,
    "producer_pins": true,
    "signed_sequential_batches": true,
    "tamper_rejected": true
  },
  "counts": {"batches": 3, "log_tracker_rows": 3,
    "no_content_nodes_scanned": 331, "no_content_violations": 0, "normalized_events": 3}
}
```

## Mutation battery — all killed

| Mutant | Result |
| --- | --- |
| M1 (scanner→no-op) | EXC:TypeError |
| M2 (`prohibitedPath` stub) | `TestProhibitedPathMatchesNestedEquivalentUnderAllowedKey` FAILS |
| M3 (`prohibitedValue` stub) | `TestSyntheticVoiceRejectsContentInjectedIntoAllowedScalar` FAILS (wrong error message) |
| M4 (chain broken, ctrl) | FAIL: `prev_batch_hash_chain=False` |
| M5 (zero tracker rows) | FAIL: `log_tracker_determinations_persisted=False` |
| M6 (continuity starved) | EXC:AttributeError |
| M_validatePins (E2E) | FAIL: `content_injection_rejected=False` |

## Branch shape vs new master

```
8 files changed, 489 insertions(+), 0 deletions(-)
```

Additions-only. Collector core untouched. Collector core isolation test passes.

## Holds

Synthetic fixtures only. No platform access, no VAPI contact, no live or sample real
call data. No merge, shared-environment or production promotion. VAPI hold unchanged.
This APPROVED verdict does not authorize deployment.
