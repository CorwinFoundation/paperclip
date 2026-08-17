# BEAAA-20074 — QA gate R3, candidate `0cd9157a`

**Verdict: APPROVED.** All seven items are closed with mutation proof. Item 1 — the
deciding item — is now proved against both the key vector and the value vector.

| field | value |
| --- | --- |
| Candidate | `0cd9157a65501c99cac4ddb892cfec35d3f96b42` |
| Subject | `BEAAA-20073 reject content in voice identity values` (authored 2026-08-17) |
| Branch tip | confirmed by authenticated `git ls-remote` against `CorwinFoundation/are-scanner` |
| Master base | `fb2cf1ef` (current master tip; branch is 4 commits ahead, all new) |
| Superseded | `b64ee660` → `0cd9157a` |
| Worktree | detached at `0cd9157a`, `/tmp/qa20074-0cd9157a` |

## Item-by-item

| # | Item | Result | Proof |
| --- | --- | --- | --- |
| 1 | Stem match at every depth, recursive allowlist, closed `producer_pins` | **CLOSED** | all 4 R2 leak values rejected with `prohibited voice content value: session_id`; M3 killed |
| 2 | `no_content_recursive` reports a measurement | CLOSED (R2, regression-confirmed) | M1 scanner→no-op: EXC:TypeError |
| 3 | Nested-under-allowed-key case kills a `prohibitedPath` mutant | CLOSED (R2, regression-confirmed) | M2 stub: `TestProhibitedPathMatchesNestedEquivalentUnderAllowedKey` FAILS |
| 4 | Invariant on a CI path that collects it | CLOSED (R2, regression-confirmed) | `passive-monitor-demo.yml` + `pr-unit-tests.yml` both real |
| 5 | Log Tracker driven by normalized events | CLOSED (R2, regression-confirmed) | M5 zero rows → `log_tracker_determinations_persisted=False` |
| 6 | `continuity` independently measured | CLOSED (R2, regression-confirmed) | M6 starved → EXC (harness exits unclean, distinct from chain failure) |
| 7 | Rebased, exact SHA named | CLOSED (R2, regression-confirmed) | base `fb2cf1ef` is current master; SHA named by CTO and verified |

## Item 1 — closed against the value vector

R3 adds `prohibitedValue()` which checks every allowed identity-field value against the
same stem list used for keys, plus URI (`://`), email (`@`), and a phone-number regex.
The opaque-token constraint (`voiceEventIDPattern` / `voiceSessionIDPattern` / exact
matches for `agent_id`, `deployment_id`, `outcome_status`) additionally locks the shape
of every identity field to non-PII fixture tokens.

`prohibitedValue` runs **before** the charset check in the field loop, so the error is
`prohibited voice content value: <field>`, not `has invalid format`. The new Go test
`TestSyntheticVoiceRejectsContentInjectedIntoAllowedScalar` checks the exact error
message — charset-only rejection would produce a different message and the test would
catch it.

### R2 leak probe re-run on R3

| value | field | error (R3) |
| --- | --- | --- |
| `555-010-9999` (caller phone) | `session_id` | `prohibited voice content value: session_id` |
| `14155550123` (E.164) | `session_id` | `prohibited voice content value: session_id` |
| `cdn.example.com:transcript.abc123` | `session_id` | `prohibited voice content value: session_id` |
| `caller.jane.doe-at-example.com` | `session_id` | `prohibited voice content value: session_id` |
| `session-5550109999` (opaque-token-compliant phone) | `session_id` | `prohibited voice content value: session_id` |
| `123-45-6789` (SSN) | `session_id` | `synthetic voice identity fields must use opaque fixture tokens` |

All four R2 leak classes reach the Go boundary and are rejected before a signed batch
is written. The SSN (3-2-4 format) is not caught by the phone regex but is caught by
the opaque token, which requires `^session-[a-z0-9]+(?:-[a-z0-9]+)*$` — the hyphenated
SSN doesn't match. Defense in depth is preserved.

`session-5550109999` (phone digits that satisfy the opaque token pattern) is caught by
the phone regex inside `prohibitedValue` — the `-` before the digit sequence acts as
the required `[^0-9]` boundary in the regex.

### Mutation M3 (`prohibitedValue` stub → always return false)

```
FAIL: TestSyntheticVoiceRejectsContentInjectedIntoAllowedScalar
  content case "555-010-9999" rejected for non-content reason:
  synthetic voice identity fields must use opaque fixture tokens
```

The test expects `prohibited voice content value: session_id` in the error. Stubbing
`prohibitedValue` falls through to the opaque token error — wrong error message,
test fails. `prohibitedValue` is independently load-bearing in the Go unit test.

## Go tests — all 23 pass

```
ok  backbond/are-collector/cmd/are-collector   (3/3)
ok  backbond/are-collector/internal/adapter    (4/4)
ok  backbond/are-collector/internal/collector  (6/6)
ok  backbond/are-collector/internal/egress     (3/3)
ok  backbond/are-collector/internal/source     (5/5)
ok  backbond/are-collector/internal/triggers   (8/8)
ok  backbond/are-collector/tests               (1/1)
```

## E2E — PASS, all 9 assertions true

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
  "counts": {
    "batches": 3,
    "log_tracker_rows": 3,
    "no_content_nodes_scanned": 331,
    "no_content_violations": 0,
    "normalized_events": 3
  }
}
```

`content_injection_rejected` is a new assertion added in R2/R3 that probes the four
R2-identified PII values directly. All four reach the Go boundary and are rejected.

## Mutation battery

| Mutant | Target | Result |
| --- | --- | --- |
| M1 | Python scanner → no-op | EXC:TypeError (harness red) |
| M2 | `prohibitedPath` stub | `TestProhibitedPathMatchesNestedEquivalentUnderAllowedKey` FAILS |
| M3 | `prohibitedValue` stub | `TestSyntheticVoiceRejectsContentInjectedIntoAllowedScalar` FAILS (wrong error message) |
| M4 (ctrl) | Chain broken | FAIL: `prev_batch_hash_chain=False` |
| M5 | Zero tracker rows | FAIL: `log_tracker_determinations_persisted=False` |
| M6 | Continuity starved | EXC:AttributeError (harness exits unclean) |
| M_validatePins | `validateProducerPins` stub (E2E) | FAIL: `content_injection_rejected=False` |

M_validatePins: the Go unit test `TestSyntheticVoiceRejectsProducerPinValueInjection`
passes incidentally with `validateProducerPins` stubbed (the test fixture's `event_id="v1"`
triggers the opaque token error before validateProducerPins matters). The E2E catches it
cleanly because the E2E fixture uses opaque-token-compliant values. `validateProducerPins`
is independently load-bearing at the E2E level. Noted, not a blocker — no leak path
exists.

## Branch shape

```
8 files changed, 489 insertions(+), 0 deletions(-)
```

All new files. No modifications to existing Collector core. Collector core untouched:
`TestCollectorDoesNotImportScoringOrReceiverCode` passes.

## Holds

Synthetic fixtures only. No platform access, no VAPI contact, no live or sample real
call data. No merge, shared-environment or production promotion. VAPI hold unchanged.
This APPROVED verdict does not authorize deployment.
