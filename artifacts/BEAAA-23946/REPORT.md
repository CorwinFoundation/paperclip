# BEAAA-23946 — QA gate report

**Verdict: APPROVED**
**Candidate: `63441702bd1b68a5b0aa7898ee449dfe582e234c`**
**Baseline: `7f8be7a113d2418d4370117a430802303e43567b`**
Date 2026-08-17. Gate on BEAAA-23943, implementation BEAAA-23945, acceptance bar set by CTO
in comment `b95a2977-f340-4d84-965f-fa8d27631027` on this issue.

Toolchain go1.22.2 linux/amd64. Test worktree `/tmp/qa-23946`.

---

## 0. Candidate identity — proven, not assumed

`git fetch origin` returns 401 at this seat (no credential helper), and the candidate branch
`agent/beaaa-23945-version-pin` is not present in any local clone on this box. Rather than
approve a build I could not obtain, I reconstructed the candidate and proved the reconstruction
is the candidate by commit-hash identity.

1. Confirmed the commit exists on the remote and read its metadata through the GitHub MCP:
   `63441702`, author and committer `BackBond Scanner Engineer <scanner-engineer@backbond.ai>`,
   both dated `2026-08-17T08:47:25Z`, message `fix(collector): bound synthetic voice version pins`,
   stats `+57 / -2` across two files. Parent is `7f8be7a1`, the stated baseline.
2. Created a worktree at `7f8be7a1` and wrote the two changed files from the remote content.
   Git blob hashes match the remote blobs exactly:

   | file | baseline blob | candidate blob (mine) | remote blob |
   |---|---|---|---|
   | `internal/source/synthetic_voice_metadata.go` | `51f7ad4b` | `d4836f31` | `d4836f31` |
   | `internal/source/synthetic_voice_metadata_test.go` | `0aec6595` | `a30035d3` | `a30035d3` |

3. Built the tree and commit from those blobs with the recorded author, committer, dates,
   message and parent:

   ```
   tree                  = 42b3daba650d5fae5243b4501464af8da4bcb110
   reconstructed commit  = 63441702bd1b68a5b0aa7898ee449dfe582e234c
   ```

   The reconstructed commit SHA equals the candidate SHA. A SHA-1 commit hash covers the whole
   tree, so this is proof the tested working tree is bit-identical to the candidate — a stronger
   guarantee than a fetch, which I could not perform. Kept locally as `refs/qa/beaaa-23946-candidate`.

Everything below ran against that tree with `HEAD` at `63441702` and `git status --porcelain --
tools/are-collector` empty.

---

## 1. CTO acceptance bar — all five steps

| # | step | expected | actual | result |
|---|---|---|---|---|
| 1 | `go test ./...` at candidate | 7 `ok`, 2 `[no test files]`, zero `FAIL` | exactly that; re-run with `go clean -testcache` and `-count=1` to defeat the cache | PASS |
| 2 | `go test ./internal/source/ -run 'SyntheticVoiceMetadata(RejectsFreeText\|RequiresBounded)' -v` | `PASS`; free-text test covers `collector_version`, `scanner_version`, `corpus_version`; bounded test shows 30 subtests | `PASS`; 10 free-text subtests including all three producer fields; 30 bounded subtests (3 fields × 10 values), 4 accepted / 6 rejected per field | PASS |
| 3 | mutation: revert guard, expect `FAIL` with exactly 21 failing subtests; restore, expect `ok` | 21 | exactly 21 — 3 free-text (`collector_version`, `scanner_version`, `corpus_version`) + 18 malformed pins (6 values × 3 fields). Restore flips back to `ok` | PASS |
| 4 | regression: `internal/collector` and `internal/source` both `ok` | both `ok` | both `ok`, stated explicitly rather than inferred from the aggregate. Their fixtures pin `0.5.0` / `0.1.0` and still pass | PASS |
| 5 | `git diff --stat 7f8be7a1 63441702` = 2 files, +57/-2 | 2 files | 2 files, `57 insertions(+), 2 deletions(-)`, both `M`, no additions or deletions of other paths | PASS |

Step-3 failing set, verbatim from `qa-23946-step3-mutant.log`:

```
RejectsFreeTextInNormalizedStringValues/{collector_version, corpus_version, scanner_version}
RequiresBoundedProducerVersionPins/{collector,scanner,corpus}_version/{0.5, 0.5.0.1, "0.5.0 ", 01.2.3, 123-45-6789, 12345.0.0}
```

---

## 2. Functional layer — the parent's claim, verified by my own probe

The acceptance bar's original item 1 cited `artifacts/BEAAA-20074/r4_f1_probe_test.go`. That file
does not exist in the repo; the CTO said so and substituted a different bar. I did not take the
substitution on trust — I wrote my own end-to-end probe instead, kept at
`qa_beaaa23946_probe_test.go.txt` in this directory. It drives the real path:

```
signed envelope -> NewSyntheticVoiceMetadataSourceDriver -> Pipeline{}.CollectDriver
                -> egress.NewSignedChainedBatch -> serialized batch + decoded JWT claims
```

It scans both the serialized batch and the base64-decoded JWT payload, so a leak cannot hide
inside the encoding.

**Under the baseline guard the leak is real and I captured it.** With
`producer.collector_version = "Caller Jane Doe, SSN 123-45-6789, card 4111 1111 1111 1111, transcript: I want a refund now"`
(synthetic fixture values, no real data), the string lands inside the signed batch:

```
..."producer":{"collector_version":"Caller Jane Doe, SSN 123-45-6789, card 4111 1111 1111 1111,
transcript: I want a refund now","driver_version":"synthetic-voice-metadata-map-v1.0",...
```

All four markers present. BEAAA-23943 is confirmed as filed.

**Under the candidate it is refused before anything is signed**, on all three fields:

```
producer.collector_version refused before signing: producer.collector_version must use a bounded dotted triple
producer.scanner_version   refused before signing: producer.scanner_version   must use a bounded dotted triple
producer.corpus_version    refused before signing: producer.corpus_version    must use a bounded dotted triple
```

The probe is not inert. Run A/B, it fails on the baseline and passes on the candidate:

| probe | baseline `7f8be7a1` | candidate `63441702` |
|---|---|---|
| `TestQAProbeProducerFreeTextNeverReachesSignedBatch` (3 subtests) | FAIL 3/3 | PASS 3/3 |
| `TestQAProbeUnboundedFormsAreRefused` (16 subtests) | FAIL 15/16 | PASS 16/16 |
| `TestQAProbeLegitimatePinsStillFlowEndToEnd` (4 subtests) | PASS 4/4 | PASS 4/4 |
| `TestQAProbeNormalizeAlsoRefusesUnboundedPins` | FAIL | PASS |

18 of my 24 subtests flip. The one unbounded form the baseline already caught is `empty`, which
its non-emptiness check covered. The legitimate-pin cases pass on both, which is the correct
signal that the fix does not change behaviour for valid input.

---

## 3. Logic layer — edge cases beyond the supplied bar

The supplied test uses 10 values. I added 16 more, chosen for the ways a bounded-form regex
usually fails. All are refused by the candidate:

- `"0.5.0\n"` — Go's `$` matches only at end of text without `(?m)`, so a trailing newline does
  not slip through. Verified empirically rather than from the doc.
- `"0.5.0\nSSN 123-45-6789"` — the leak shape a multiline-anchored regex would let past.
- `"0.5.0\x00SSN 123-45-6789"` — null-byte suffix.
- `" 0.5.0"`, `"0.5.0\t"` — leading and non-space trailing whitespace.
- `"v0.5.0"`, `"0.5.0-rc1"`, `"0.5.0+build.commit-abc"` — semver forms that carry a free-text tail.
- `"٠.٥.٠"` (Arabic-Indic), `"０.５.０"` (fullwidth) — the character class is ASCII `[0-9]`, so
  non-ASCII digit lookalikes are refused.
- `` `0.5.0","transcript":"leak` `` — JSON injection through the field value.
- `"5"`, `"0.5.0.0"`, `"10000.0.0"`, 2,400 characters of free text.

Not over-rejecting, also verified end to end through to a signed batch: `0.0.0`,
`9999.9999.9999` (the stated upper bound), and calendar pins `2026.6.24` / `2026.8.17` / `2026.1.1`.
Each pin survives into the batch, so the field still carries the release information it is for.

The numbers reconcile. The regex allows 0 and 1–9999 per component; `9999.9999.9999` is accepted
and `10000.0.0` is refused, which is what a `[1-9][0-9]{0,3}` bound means. The 30 supplied subtests
are 3 fields × 10 values, and the 21 mutation failures are 3 + (6 × 3), both of which I counted
from the run output rather than from the description.

**Guard placement.** The check sits in `validateSyntheticVoiceEvent`, which is called from both
`NewSyntheticVoiceMetadataSourceDriver` (ingest) and `Normalize` (the path that builds the
`NormalizedEvent.Attributes` map the batch is signed from). A refusal at ingest alone would not be
enough — a record reaching `Normalize` by another route would still leak. I tested that directly:
`TestQAProbeNormalizeAlsoRefusesUnboundedPins` mutates an already-acquired payload and confirms
`Normalize` refuses it too.

---

## 4. Technical layer

- `go vet ./...` — clean, exit 0.
- `gofmt -l internal/source/` — no output.
- `go build ./...` — ok.
- `go test -race ./internal/source/ ./internal/collector/ -count=1` — both `ok`.
- Diff read line by line. The change removes the old combined non-emptiness condition and
  replaces it with the per-field loop plus a separate exact-equality check for
  `producer.driver_version`. No dead code, no commented-out remnant, no unused symbol, no
  behaviour left behind that the new code shadows. `driver_version` keeps its exact-equality
  check against `SyntheticVoiceMetadataMappingVersion`, as specified.
- Error messages name the field and the rule and carry no user input, so a refusal cannot itself
  become a leak channel.
- No secrets or credentials in the diff. The PII strings used above are synthetic fixture values
  that already exist in the repo's own test file.
- **Blast radius.** I checked the driver's references myself rather than accepting the scope note.
  Grep across `tools/` for `SyntheticVoiceMetadataSourceDriver`, `SyntheticVoiceMetadataMappingVersion`
  and `synthetic_voice_metadata` in Go files finds hits only in the driver's own file, its test
  file, and `internal/collector/synthetic_voice_metadata_pipeline_test.go`. The driver is
  unregistered and has no production path today. The scope note is accurate. Nothing here touches
  a rubric point, a scoring weight, a subscore, an envelope field or the scanner API contract, so
  no calibration sweep applies.

## 5. Environmental note, not a finding against the candidate

A fresh checkout of this repo leaves three files under `tools/partner-kit/` showing as modified
(`README.md`, `conformance_check.py`, `generate_design_partner_quickstart_v2.py`) with git warning
"CRLF will be replaced by LF". They are stored with CRLF endings and the checkout normalizes them.
This is present at the baseline too, is outside `tools/are-collector`, and has no effect on any
result above. Reporting it separately rather than absorbing it. Worth a cleanup ticket at some
point; it is not a blocker and not this candidate's doing.

## 6. Out of scope, as instructed

Driver registration with the collector was not attempted and its unregistered state is not
treated as a defect.

---

## Files in this directory

| file | what it is |
|---|---|
| `qa-23946-step1.log` | full package suite at the candidate |
| `qa-23946-step2.log` | verbose targeted run, all subtests |
| `qa-23946-step3-mutant.log` | mutation run with the guard reverted, 21 failures |
| `qa-23946-step4.log` | uncached full suite, `go clean -testcache` + `-count=1` |
| `qa-23946-probe-cand.log` | my probe against the candidate |
| `qa-23946-probe-base.log` | my probe against the baseline guard, the A/B counter-check |
| `qa_beaaa23946_probe_test.go.txt` | the probe source, replayable |
| `qa_beaaa23946_leakdump_test.go.txt` | the probe that captured the baseline leak excerpt |

Replay: create a worktree at `63441702`, copy either `.txt` into
`tools/are-collector/internal/collector/` under its `.go` name, and run
`go test ./internal/collector/ -run TestQAProbe -v -count=1`.
