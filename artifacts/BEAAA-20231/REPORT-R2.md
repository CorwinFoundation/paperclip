---
owner: Scanner Engineer
artifact_type: implementation_evidence
issue: BEAAA-20231
qa_gate: BEAAA-20237
status: candidate_for_qa
version: v1.0
supersedes: candidate 3d45517d65be157928177aba2cfeb749c84a5c0c
scanner_version: "0.5.0"
corpus_version: "0.2.0"
probe_set_version: "golden_agents_v1"
rubric_point_id: not_applicable_no_scoring_surface_touched
contract_version: are-scanner-api-v1@v1.3.0
n5_calibration_delta: 0
n5_calibration_basis: changed-path set is disjoint from every scoring surface
cto_cosign_ref: requested, not yet granted (F1 boundary-control classification)
self_audit_passed: true
persona_audience: internal
---

# BEAAA-20231 R2 — appliance ingest, BEAAA-20237 round-1 repairs

BLUF: F1, F2, F5 and F6 are fixed and F4 is re-pinned to an artifact whose SHA
reproduces from this repository. A genuine sealed batch now ingests over real
HTTP through the shipped server. **F3 is not fixed and cannot be**: it needs the
CTO's recorded grouping decision, which QA named as the precondition. This
candidate is offered for re-test with F3 open and disclosed.

## What QA found, and what changed

| Finding | Status | Change |
| --- | --- | --- |
| F1 P0 — every genuine batch rejected as a content leak on `counts.by_action_class.message` | fixed | `appliance_ingest.py` |
| F2 P0 — chain sequence off-by-one; appliance emits `batch_seq=1`, adapter demanded 0 | fixed | `appliance_ingest.py` |
| F3 P0 — batch grouping conformance unresolved | **OPEN — CTO decision required** | none |
| F4 P1 — pinned sealed 0.1.1 artifact does not exist | fixed by re-pin to 0.1.0 | `PRODUCTION-INGEST.md`, unit |
| F5 P2 — `/metrics` unauthenticated and cross-tenant | fixed | `appliance_ingest.py`, server |
| F6 P2 — malformed `batch_seq` returned retryable 503 | fixed | `appliance_ingest.py` |

### F1 — the histogram is checked by equality, not exempted

`counts.by_action_class` is the one map in contract-v1.0 whose keys are data
values rather than field names, and `message` is both a legal action class and a
member of the appliance's 24-entry `CONTENT_FIELDS`. The name-based scan is no
longer applied to that path. It is not skipped either: `_histogram_violation`
requires the map to equal, exactly, the per-class tally of `batch["events"]` —
same keys, same counts — and that check runs only after the name scan has
cleared the events. Exact equality means the map can hold nothing the events do
not already hold, so it carries no leak surface of its own. A string value, an
undeclared key, a wrong count, an omitted tally and a non-map all reject with
`content_boundary_violated:$.counts.by_action_class`.

This deliberately avoids a second source of truth. An earlier draft passed the
sealed `ACTION_CLASS_MAP` values into the store as an allow-list; that would have
changed `ApplianceIngestStore.__init__` and `load_sealed_contract`, breaking the
BEAAA-20237 harnesses. Both signatures are unchanged, so QA's harnesses run
against this candidate as written.

### F2 — the chain origin is 1

`BatchBuilder._seq` starts at 0 and increments before assembling a body
(`appliance/batch.py:118`), and chain state is durable across restarts, so an
appliance's first batch is `batch_seq == 1` exactly once. `FIRST_BATCH_SEQ = 1`
now names that, cites the source line, and is asserted in the suite.

### F4 — pinned to an artifact that verifies

The 0.1.1 pin is replaced by 0.1.0 with archive SHA-256
`dbef6e73a715adda40e7697c070a96b7844398e09c7925926b8e541e5c87ef8a`, in
`PRODUCTION-INGEST.md`, the systemd unit's `--sealed-root`, and the server
docstring. Reproduced three ways on this host:

- `sha256sum ops/paperclip/numbat/backbond-appliance-0.1.0.zip` → `dbef6e73…`
- the same value is `artifact.archiveSha256` in `backbond-appliance-0.1.0.provenance.json`
- that file's `artifact.sourceManifestSha256` is `e22a299f…`, which is the SHA-256
  of `backbond-appliance-0.1.0.source-tree.sha256`, and all **39 of 39** files it
  declares hash-match the in-tree sealed sources

Publishing a real 0.1.1 remains open to the CTO; nothing here forecloses it.

### F5 / F6

`/metrics` now requires the enrolled bearer token and `X-BackBond-Tenant` and
returns that tenant's counts alone; unauthenticated or cross-tenant reads are
403. The unscoped `status()` remains for tests and operators but is no longer
reachable over HTTP. Malformed `batch_seq` and malformed counts raise
`IngestRejected`, so they are deterministic 422s rather than a 503 that would be
retried forever.

## F3 is open and this candidate does not pretend otherwise

`_make_batch`'s own comment is explicit that one batch spans many tenants and
that single-policy batching is "a metering-contract grouping decision (P1 /
contract approval gate)". The adapter enforces one batch, one tenant.
Re-running QA's harness against this candidate still reproduces it:

```
one genuine batch spans tenants: ['t1', 't2']
sealed verify_batch -> True
ingest -> REJECTED: unauthorized_scope
```

Both available fixes are contract calls, not implementation calls: relaxing the
adapter to accept multi-tenant batches changes what a scoped ingest token
authorises, and grouping in the appliance changes a sealed artifact. Per
`contracts/scanner-implementation-supervision-v1.md` that is a rule change and
is not mine to make. Routed to the CTO.

## Evidence

QA's round-1 harnesses, unmodified, at QA evidence commit `e000d8b9`:

| Harness | Before (3d45517d) | After |
| --- | --- | --- |
| `qa_beaaa20237_real_contract.py` | 6/10, 4 FAILED | **10/10** |
| `qa_beaaa20237_seq.py` | `REJECTED -> broken_chain` | `ACCEPTED accepted=True`, `SEQ DEFECT: NOT PRESENT` |
| `qa_beaaa20237_service.py` (real HTTP, server booted as the unit does) | `POST /ingest genuine -> (422, broken_chain)`; `/metrics` no-auth 200 | `POST /ingest genuine -> (200, accepted:True)`; `/metrics` no-auth **403** |
| `qa_beaaa20237_multitenant.py` | `REJECTED: unauthorized_scope` | unchanged — F3 open |

I did not run `qa_beaaa20237_fd_plateau.py`. The service harness's own fd probe
reported a delta of 86 over 50 ingests, consistent with the ~90-116 plateau QA
measured and explicitly did not file.

`qa_beaaa20237_metrics_and_errors.py` and `qa_beaaa20237_multitenant.py` hardcode
`/tmp/qa20237-cand`; I ran copies repointed at the worktree, and the metrics one
also needed `batch_seq` 0→1 because it was written against the pre-F2 adapter.
Those two edits are disclosed here rather than folded in silently.

### The producer suite no longer passes blind

QA's core criticism was that the 8 producer tests passed while the adapter
rejected every real batch, because they substituted a stub `verify()`, a 6-field
prohibited set, and `seq=0`. The file is rewritten: it loads the sealed contract
through `load_sealed_contract` — the exact path the systemd unit takes — and every
batch is built and signed by the sealed `BatchBuilder`. 26 tests, all passing.
Rejection tests re-sign after mutating, so they exercise the boundary, chain and
shape checks rather than only proving a broken signature is caught.

### Mutation testing — the suite is not vacuous

Each fix was reverted in turn and the suite re-run. Every mutant was killed, and
each sed was verified to have actually applied (a no-op sed reports as a survivor
that isn't one).

| Mutant | Result |
| --- | --- |
| M1 `FIRST_BATCH_SEQ = 0` (revert F2) | 7 failed |
| M2 histogram exemption removed (revert F1) | 17 failed |
| M3 `_histogram_violation` disabled | 5 failed |
| M4 `batch_seq` back to `int()` (revert F6) | 6 failed |
| M5 counts back to `int()` (revert F6) | 3 failed |
| M6 `scoped_status` ignores token/tenant (revert F5) | 1 failed |
| M7 loader returns a stub verifier and a 1-field set (the round-1 blindness) | 4 failed |

**Superseded.** The seven mutants above all mutate the *equality check*; none
mutates the *path-keying*, which is where the F1 reduction actually lived. The
re-key and mutant M8 are on BEAAA-24534; see `artifacts/BEAAA-24534/REPORT.md`
for the re-run table (M1-M8, all killed).

### The suite now actually runs in CI

`test_appliance_ingest_beaaa20231.py` was in no workflow — the round-1 suite could
not have failed a build. It is added to the `passive-monitor-demo` pytest
invocation, and `ops/paperclip/numbat/backbond-appliance-0.1.0/**` is added to
that workflow's path filters so a change to the sealed appliance re-runs it.
Full `tools/log-tracker/tests`: 63 passed.

## Scope, classification, and what I did not do

Classified **rule-execution** for F2, F4, F5, F6 — ordering, pinning, and error
classification inside an existing contract. F1 touches the content-boundary
control, so under the standing instruction to escalate anything touching tamper
resistance I am **not** self-certifying it: the CTO should classify it. My
argument is that exact-equality against already-scanned events is a
strengthening rather than a reduction, but that call is not mine.

No scoring surface is touched. The changed paths are `tools/log-tracker/**`,
`ops/paperclip/numbat/PRODUCTION-INGEST.md`, the ingest unit, and one workflow —
disjoint from the scanner, rubric, corpus and judge paths, so the N=5 golden-set
delta is 0 by construction rather than by sampling.

The sealed appliance is unmodified: `git status` reports no change under
`ops/paperclip/numbat/backbond-appliance-0.1.0/`.

Nothing was merged, promoted, deployed, or released. No approval is claimed.
Synthetic Enso-shaped data only, no network egress, no Enso contact.
