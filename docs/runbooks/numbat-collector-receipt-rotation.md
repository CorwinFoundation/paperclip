# Numbat collector — receipt-file rotation on a `ReceiptVersion` bump

**Owner:** Scanner Engineer (agent `a48f9b14-e8ef-4bfd-9297-61d179b2cf10`), BEAAA-24493.
**Applies to:** `tools/are-collector`, `-source numbat`.
**API contract pin:** `contracts/are-scanner-api-v1.md@v1.0` (untouched by this behaviour).
**Behaviour documented at:** `b74dbe02a70ea417f0316d3dc86b0a31e58ce176`, QA-approved on BEAAA-23695.
**Current `ReceiptVersion`:** `numbat-reconciliation-receipt-v3` (`internal/reconciliation/receipt.go`).

## BLUF

An operator who carries an existing receipt file across a collector upgrade that bumps
`ReceiptVersion` gets a hard failure on the first collection. Rotate the file first: archive it,
then point `-reconciliation-receipt` at a new path. The collector exits non-zero, emits no batch,
and appends no row until you do. This is intentional and is not to be relaxed.

## When this applies

Any upgrade that changes the `ReceiptVersion` constant in
`tools/are-collector/internal/reconciliation/receipt.go`. It is not specific to one version seam.
The guard compares the constant against every row it reads, so v2 to v3 and v3 to v4 behave
identically; v3 to v4 is already queued on BEAAA-24444.

You need this step only when the receipt file predates the upgrade. A collector pointed at a new
or absent path writes its own first row and needs nothing.

Check before upgrading:

```sh
grep -o 'numbat-reconciliation-receipt-v[0-9]*' receipt.ndjson | sort -u
```

If that prints anything other than the `ReceiptVersion` the incoming collector writes, rotate.

## The error you get if you skip it

Verbatim, from `internal/reconciliation/receipt.go:107`. The path and the two version strings are
substituted; the rest is fixed and greppable:

```
receipt file /var/lib/backbond/receipt.ndjson holds a "numbat-reconciliation-receipt-v2" row but this collector writes "numbat-reconciliation-receipt-v3": rotate the receipt file (archive it and point -reconciliation-receipt at a new file) before collecting
```

Grep for the stable substring:

```sh
grep -F 'but this collector writes' collector.log
```

Alongside it: exit code 1, zero bytes on stdout, no batch emitted, and the receipt file unchanged
byte for byte. The refusal happens before any write, so there is no partial state to clean up.

The flag the message names, `-reconciliation-receipt`, is the one the collector defines, so the
remediation is directly actionable: archive the file, point that flag at a new path, collect. The
section below is that procedure in full.

## The rotation

Three steps, on the collector host, with the collector stopped.

1. **Archive the existing file.** Move it aside under a name that records the schema it holds and
   the date. Do not delete it and do not edit it in place.

   ```sh
   mv /var/lib/backbond/receipt.ndjson \
      /var/lib/backbond/receipt.ndjson.v2-archived-20260819
   ```

2. **Point the collector at a new path.** Change `-reconciliation-receipt` in the unit file, the
   Kubernetes manifest, or whatever launches the collector.

   ```sh
   are-collector -mode fixture-smoke -source numbat \
     -reconciliation-receipt /var/lib/backbond/receipt-v3.ndjson \
     ...
   ```

3. **Collect once and confirm.** The new file should hold exactly one row at the new version.

   ```sh
   head -1 /var/lib/backbond/receipt-v3.ndjson | grep -o 'numbat-reconciliation-receipt-v[0-9]*'
   ```

### What the archived file is still for

It remains the reconciliation record for the rows it holds, and it stays readable and valid after
rotation. Rotation ends its role as the collector's append target; it does not retire it as
evidence. Retain it under the same policy as the active file. Reconciling a batch collected before
the upgrade means reading the archive, not the new file.

Do not concatenate an archive back into an active receipt file. That recreates the mixed-version
file the guard exists to reject, and the next collection will fail on it.

## Two behaviours that surprise operators

**A stale row anywhere in the file fails the whole file.** The guard scans every row, not just the
first. A file whose first rows are current-version still fails if one old row sits further down.
Partial rotation, such as trimming the head of the file, does not clear it.

**A replay is refused too.** Re-collecting a batch the file already records as a current-version
row still fails, because the scan reaches the stale row before the matched row is used. Rotation is
required before the replay, not as a way to recover from one.

## Why the guard is not relaxed

Rows written before v3 carry no `idempotency_key`. Every one of them reads as non-matching, so a
current collector would silently re-emit a batch the file already records. Negative control, guard
deleted and the same input: a v4 collector appended to a file holding a v3 row, exited `rc=0` and
emitted the batch, leaving one file holding `['v3', 'v4']` with nothing marking the seam.

The alternative of matching legacy rows on `ordered_batch_hashes[0]` was reviewed and **rejected on
BEAAA-24448**. That field is the emitted batch identity. On the v2 wire it commits to `issued_at`,
so it moves on every replay; on the v1 wire it is the event digest alone, so two distinct batches
over the same events collide. Those are exactly the key semantics the idempotency key was
introduced to remove, and a fallback would reintroduce both.

Auto-rotation is out of scope and is not to be added without its own review. Silently rotating a
reconciliation record is the class of quiet behaviour this pilot exists to prevent: the operator
would lose the one signal that the schema changed underneath them.

## Verification

Seven cases driven end to end against binaries built from `b74dbe02`, recorded in
`BEAAA-24493-live-evidence/`:

| Case | Setup | Result |
|---|---|---|
| 1 | Clean file, current collector | `rc=0`, one v3 row, batch emitted |
| 2 | File holds a v2 row | `rc=1`, no batch, file unchanged |
| 3 | Remediation followed literally, as the message words it | `rc=0`, new file written, archive intact |
| 4 | Archive, then new path via `-reconciliation-receipt` | `rc=0`, archive intact |
| 5 | v4 collector against a v3 row | `rc=1`, same message, version-agnostic |
| 6 | Stale row second in file; also a pure replay | `rc=1` in both |
| 7 | Negative control: guard deleted, v4 collector, v3 row | `rc=0`, appended, file left holding `['v3', 'v4']` |

No receipt row in any case was hand-crafted. In cases 5 and 7 the v3 row is a genuine row written
by the `1b7b0b3f` binary, read by a scratch build with `ReceiptVersion` bumped to v4 that stands in
for the BEAAA-24444 collector; case 7 additionally has the guard block removed. In cases 2 and 6 a
real row was written first and then rewritten to the older version to stand in for a file carried
across the upgrade.

## References

BEAAA-24493 (this runbook), BEAAA-24480 (the guard), BEAAA-24448 (fallback rejected),
BEAAA-24444 (the v3 to v4 bump), BEAAA-23693 (collector entrypoint), BEAAA-23695 (QA gate),
BEAAA-24497 (error-text flag-name fix, shipped).
