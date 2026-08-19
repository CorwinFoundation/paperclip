# BEAAA-24725 candidate report

BLUF: the ingest envelope is closed against unsigned riders, the P1 observability
surface is in place, and the clean-host install plus rollback drill has been run
end to end under systemd. Three BEAAA-20237 P1 acceptance checks close on this
evidence. The drill found four defects in the install procedure and one in the
rollback text; all five are fixed in `PRODUCTION-INGEST.md` and the drill was
re-run clean afterwards.

- Candidate: `a0ca6858146d6d8c7c2a1f43047f57f6ca84fe28`
- Branch: `agent/beaaa-24725-p1-envelope-observability`
- Base: `e8c5dc4463ee4cceaa98c7735c099a7a3dbb0808` (BEAAA-24534, tip of
  `origin/agent/beaaa-24534-boundary-structural-rekey`). Not rebased onto master;
  F1 co-sign for that branch is tracked on BEAAA-24478.
- Scope authority: CTO ruling on BEAAA-24623, comment `4793d039`, and
  `ADR-BEAAA-24623-p1-acceptance-scope-ruling.md`.
- Classification: **rule-change**, per the ruling. Input-acceptance validator
  expansion plus an operational surface addition, BEAAA-2198 precedent. ADR yes;
  no `are-scanner-api-v1` bump; no Underwriter co-sign; no calibration delta.
  `cto_cosign_ref`: pending, this ticket.
- Contract: `contracts/are-scanner-api-v1.md@v1.0`, unchanged. The adapter
  neither computes nor moves a BackBond Score, so the contract output shape is
  untouched.

## Part A -- the envelope is closed at the top level

The accepted top-level key set is pinned to the twelve keys a sealed 0.1.0
envelope carries: the nine `verify_batch` rebuilds the signed body from, plus
`batch_hash`, `signature`, `key_id`. Anything else is a deterministic 422
`unknown_envelope_field`. Set comparison against a pinned allowlist.

The residual was real, not theoretical. Reproduced at the base commit before
writing any code:

    verifies: True
    RIDES THROUGH -> accepted: True duplicate: False

for a batch carrying `evidence_tier`. It sits outside the signature and
`batch_hash`, so `verify_batch` returns True, and it is in neither
`CONTENT_FIELDS` nor any scanned subtree, so the name scan never looked at it.
Same batch on the candidate: `rejected: unknown_envelope_field`.

Two decisions inside the ruling worth naming, because both narrow it:

**Ordering.** The check runs *after* the content scan, not before. Both refuse
the same delivery, so the order decides only which class an injected key reports
under. Putting the envelope check first would have flipped the three BEAAA-24478
item 1 forgery regressions from `content_boundary_violated` to
`unknown_envelope_field`, and the ticket requires that suite green **unmodified**.
It is still "after signature verification and before the write transaction" as
ruled. A key whose subtree carries prohibited content is also better reported as
the leak it is.

**The reason names no field.** `unknown_envelope_field` carries no suffix. The
sender named that key, and a sender-chosen string echoed into a rejection is the
same content channel the rendered path was. Mutant M4 adds the field name and
dies.

**One narrowing to flag for the co-sign.** The check tests for *extra* keys, not
for exact set equality. Post-verification the two are the same test for eleven of
the twelve keys, because `verify_batch` reads them and fails closed without them
(covered by `test_a_missing_envelope_key_is_already_caught_by_the_verifier`).
`key_id` is the exception: nothing reads it, so a batch missing only `key_id` is
accepted. That is recorded as a passing test rather than hidden, because it is
not the class this closes -- dropping a field removes information, where the
residual is a sender *adding* an unsigned one. If P1 wants exact equality it is a
one-word change plus a second reason class, and that is the CTO's call, not one
to make silently inside an implementation ticket.

## Part B -- the P1 observability surface

**One structured line per request outcome**, JSON on stderr, which journald
carries under the existing unit. Live from the drill:

    {"appliance_id":"a1","batch_seq":1,"event":"appliance_ingest","outcome":"accepted","reason_class":null,"ts":"2026-08-19T21:57:17.418Z"}
    {"appliance_id":"a1","batch_seq":1,"event":"appliance_ingest","outcome":"rejected","reason_class":"unknown_envelope_field","ts":"2026-08-19T21:57:17.733Z"}
    {"appliance_id":"a1","batch_seq":1,"event":"appliance_ingest","outcome":"duplicate","reason_class":null,"ts":"2026-08-19T21:57:23.125Z"}
    {"appliance_id":"a1","batch_seq":2,"event":"appliance_ingest","outcome":"accepted","reason_class":null,"ts":"2026-08-19T21:57:23.387Z"}

`reason_class` is the class only. The ruling's reasoning about
`content_boundary_violated:$.a.b` applies identically to `appliance_id`, which
also arrives off the wire and is also named by the sender, so it is written only
when it matches `[A-Za-z0-9._:-]{1,64}`. JSON encoding already prevents a forged
newline from splitting one record into two; the alphabet stops the field being
used as free text into the operator's log. `tenant_id` never appears: the drill
greps the whole journal for the tenant and finds nothing.

**`/metrics`** keeps its bearer and `X-BackBond-Tenant` scoping and adds
`rejected_total` by class, `duplicates_total`, `unavailable_total`,
`accepted_total` and `chain_tip_batch_seq` per appliance. From the drill, after
one acceptance and one refused rider:

    {"accepted_total": 1, "batches_accepted": 1, "chain_tip_batch_seq": {"a1": 1},
     "determinations_accepted": 0, "duplicates_total": 0, "events_accepted": 2,
     "rejected_total": {"unknown_envelope_field": 1}, "unavailable_total": 0}

Cross-tenant read 403, unauthenticated read 403. Counters are process-local as
ruled, so no migration; `chain_tip_batch_seq` comes from the receipt table and
survives a restart, which is what keeps a quiet appliance distinguishable from a
stalled one after the unit bounces. Counters are keyed by the tenant the request
*claimed*, because that is the only tenant known when a delivery is refused
before enrollment resolves; reading them still requires that tenant's enrolled
token.

**One adjacent fix, required by the outcome taxonomy.** A JSON body that is not
an object used to reach `batch.get` and die inside the catch-all, so a
permanently malformed body was answered `503 persistence_unavailable`. It is now
a 422 `malformed_body`. Left alone, `unavailable_total` would have been the first
counter an operator saw lie, and the sender would retry a permanent defect
forever.

## Part C -- clean-host install and rollback drill

Ran end to end on a host prepared by nothing but the doc's steps, under real
systemd as root, against candidate `113933f0`. Transcript:
`artifacts/BEAAA-24725/drill-transcript.txt`, 156 lines, exit 0, ending
`DRILL PASSED`. Reproducible: `artifacts/BEAAA-24725/drill.sh` plus
`Dockerfile.drill` and `drill_producer.py`.

Every batch in the drill is built by the sealed `BatchBuilder` with real
Ed25519 signing and real `chain.state`, not hand-rolled JSON.

Drill results against the ruling's five steps:

| Step | Result |
|---|---|
| install from the doc only | pass, after five doc fixes (below) |
| `/healthz` 200 | `healthz http=200` |
| enrol and deliver, accepted at seq 1 | `{"accepted": true, "duplicate": false}`, one receipt row |
| stop and disable, delivery fails closed, no receipt row | `inactive`/`disabled`; client `URLError ... Connection refused`, curl exit 7; receipt rows byte-identical across the window by `diff` |
| restart, redeliver seq 1, then chain at seq 2 | `duplicate: true` then `duplicate: false`; seq 2 `prev_batch_hash` equals the pre-rollback tip |

The chain assertion is the point the ruling made, and it holds:

    chain intact across the rollback: seq 2 prev == pre-rollback tip

### What the drill found

The first pass never reached a running service. All five are fixed in
`PRODUCTION-INGEST.md` and the drill was then re-run from a fresh host, passing
on the first attempt.

1. **Sealed root path.** The archive unpacks to `backbond-appliance/`; the unit
   passes `--sealed-root /opt/backbond/appliance-0.1.0`. No rename was named.
2. **Missing `cryptography`.** The material one. `log-tracker` declares only
   `pydantic`; the service imports `appliance.batch`, which imports
   `cryptography`. The receiver exited 1 before binding a port, and
   `Restart=on-failure` restarted it every five seconds:
   `ModuleNotFoundError: No module named 'cryptography'`, `NRestarts=5` and
   climbing. The sealed tree's own `requirements.txt` covers it; the receiver
   install ran neither it nor `install.sh`.
3. **Service identity and directories.** No `backbond` user or group,
   `/var/lib/backbond` or `/etc/backbond` in the procedure. `ProtectSystem=strict`
   means the database directory has to exist and be owned before first start.
4. **Enrollment file ownership.** The doc covers the env file
   ("root-readable"), which is right for `EnvironmentFile` because systemd reads
   it as root before dropping privileges. The enrollment JSON is opened by the
   process *after* it drops to `backbond`:
   `PermissionError: [Errno 13] ... /etc/backbond/appliance-enrollment.json`.
5. **Rollback text.** "Remains intact for audit and may be reopened" named no
   steps and no failure signature. It now carries the redeliver-first sequence
   and the two outcomes that mean the database did not survive.

### One finding reported, not fixed

A service that fails on every start never reaches `failed`. With
`RestartSec=5` and systemd's default start limit of five starts in ten seconds,
the restarts are spaced far enough apart that the limit never trips, so the unit
sits in `activating` indefinitely. Observed directly at `NRestarts=5`. Ruling
item 4.3 said process-failure visibility is unchanged, so this is recorded in the
doc and left for the CTO rather than fixed by tuning the unit.

### What the drill substrate does and does not prove

The clean host is a Docker container running systemd as PID 1
(`Dockerfile.drill`). This seat has no root on the VM and no user systemd bus, so
that was the only way to run the real unit under real systemd with real
privilege dropping. It is a **test substrate, not a deployment path**: the CTO
ruled the container out of P1 scope as a deployment mechanism, and nothing here
proposes otherwise.

Proven: the install steps as written, `systemd-analyze verify` clean,
`systemctl enable/start/stop/disable`, `User=backbond` privilege dropping and the
file-permission consequences of it, journald capture of the outcome log,
`Restart=on-failure` behaviour, and every data-path claim.

Not proven: kernel-level enforcement of `ProtectSystem=strict`, `PrivateTmp` and
`NoNewPrivileges` under a `--privileged` container, and anything specific to the
production host image. One step deviates from a literal doc read for a substrate
reason and is marked in the transcript: the candidate is mounted read-only, so
the package source is copied to a writable path before `pip install`, because
setuptools builds in-tree.

## Verification

| Check | Result |
|---|---|
| `tools/log-tracker` full suite | 151 pass, three consecutive runs |
| Baseline at `e8c5dc44` | 74 pass |
| `test_appliance_ingest_beaaa20231.py` | 37 pass, file **unmodified** (`git diff` empty) |
| New `test_appliance_ingest_p1_beaaa24725.py` | 77 tests |
| Mutation table | **18/18 killed**, one mutant per named property |
| Clean-host + rollback drill | exit 0, `DRILL PASSED` |
| Concurrency test flake check | 0/10 failures |

The mutation table (`artifacts/BEAAA-24725/mutation_table.py`) refuses to report
a kill unless the same tests pass unmutated first, and patches the real file with
a guaranteed restore rather than a scratch copy, because the tests resolve the
sealed appliance relative to the repository and a copy would make every mutant
appear killed by an import error. M13 initially survived: dropping the counter
lock did not break the concurrency test at CPython's default 5ms switch interval.
The test was strengthened rather than the mutant weakened.

## Operational metadata

- `scanner_version`: not applicable. This ticket touches
  `tools/log-tracker` only; no scanner, probe, corpus or rubric file is in the
  diff.
- `corpus_version`: not applicable, same reason.
- `probe-set-version`: not applicable, same reason.
- `rubric_point_id`: none. The ingest adapter is outside the rubric.
- `N=5 calibration delta`: **not run, and correctly so.** The ruling states no
  calibration delta: the adapter neither computes nor moves a BackBond Score, and
  no golden-set input reaches it. Recorded here rather than silently omitted.
- `cto_cosign_ref`: pending on BEAAA-24725.

## Open items

1. CTO co-sign, this being a rule-change.
2. The `key_id` narrowing in Part A, above: accept as written or rule for exact
   set equality.
3. The `activating`-forever finding: reported, not fixed, per ruling item 4.3.
4. QA on the exact candidate before any promotion. Nothing here is deployed.
   BEAAA-23667's authorization is dead and unrelated; a deploy needs a new issue
   and fresh Founder exact-SHA authorization.
