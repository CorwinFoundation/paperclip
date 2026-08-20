# QA gate BEAAA-23965 — paperclip-bridge v6 write-session integrity

**Verdict: APPROVED** for the candidate bytes, R1, 2026-08-17.
Parent: BEAAA-23961. Canonical QA child: BEAAA-23965 (no sibling gate exists; this
is the first revision on this child).

## Candidate under test

Re-hashed at the start and again at the end of the run; unchanged both times.

| file | sha256 |
|---|---|
| `bridge.py` (candidate, 383 lines) | `3ca27de45a953dbfe635807f46722cbe6993053a95ba92265d9b91f3854090b2` |
| `/opt/paperclip-bridge/bridge.py` (baseline v5, 159 lines) | `9f418a93ca7c331e3a0d3af87ed9a6410b778f2f8b717bbd1ca7d38a97e12d7e` |
| `test_bridge_wsess.py` | `8375152de6096f97a1d5387c74c599b1ad8ac44345a5c54160d2f0f64c7cc8d1` |
| `test_read_route_parity.py` | `ea3ba320722b186683625efb4a05192a48482b8c09ad0dc4c8203a8530d627b0` |
| `DEPLOY.md` | `4496b618e3bc05d3b12712c169ea1eac3f8c4560469790b2d41f54114fd1b4b9` |

Root: `/mnt/paperclipdata/dot-paperclip/instances/default/workspaces/88e68019-1cb6-4c4f-83e1-9fd340fb6821/artifacts/beaaa-23961/`.

Not deployed. This gate covers the candidate bytes. Cutover needs root on the
AriClaw host, which neither the CTO nor the QA seat holds. Brand Backbone gate
does not apply: `DEPLOY.md` declares `external_scope: out`, internal audience,
and nothing here is an outward-facing asset.

### Production was not touched

`/opt/paperclip-bridge/bridge.py` still hashes `9f418a93...`, the service is the
same process (pid 2360888, uptime unbroken across the run), and `:8100` still
answers 403 to a bad token. Every suite and probe bound its own loopback ports
(8196-8199, 8290-8297) against a stub upstream.

One correction to my own method, recorded because it changed a result: probe2
initially pointed `BRIDGE_UPSTREAM` at the real API on `:3100`, so one hostile-input
case relayed a POST to `http://127.0.0.1:3100/%C2%B2%C2%B2/abcdefabcdef/api/x`
with a 15-byte test body. That is a non-existent route; it created nothing and
returned 500 from the API. I re-ran the whole case set against a stub
(`probe3.py`) and the finding below is from that isolated run, not the first one.

## Acceptance criteria

### 1. Battery 2/12 on baseline, 12/12 on candidate — PASS

Baseline scored exactly 2/12, and the two passes are exactly T7 (happy path) and
T11 (hostile token), as declared. The other ten fail:

```
T8 sid entropy: sid 'eda1e0' -> ~24 bits          T5 replay -> extra POST
T1 wnew -> 200 '021e99' (the live victim sid)     T6 stale sid still resident
T1b victim held 7 slices before, 0 after          T9 6 concurrent -> 6 POSTs
T2 19-slice overwrite -> codes [200] 'ok 55'      T10 declared-mismatch -> 201
T3 empty -> 201 {"created":1,"bytes":0}           T4 gap -> 201 138 bytes
```

The two v5 failures that matter most: T3 shows v5 POSTing a zero-byte body to a
create route, and T4 shows it POSTing a 138-byte fragment of a 950-byte body.

Candidate: 12/12, exit 0. `T9 racing wgo POSTs exactly once: 6 concurrent commits
-> 1 upstream POSTs, statuses [201, 409, 409, 409, 409, 409]`.

I mutation-tested the battery so its 12/12 is not vacuous. Nine mutants, each
disabling one v6 guard; none survives silently:

| mutant | guard removed | killed by |
|---|---|---|
| M1 | wgo replay guard | T5 |
| M2 | `in_flight` claim | T9 |
| M3 | contiguity check | T4 |
| M4 | empty-session check | aborts the run (see note) |
| M5 | wput dup-seq guard | T2 |
| M6 | sid back to `token_hex(3)` | T8 |
| M7 | wnew collision guard | T1 and T1b |
| M8 | `_wgc()` in wput | T6 |
| M9 | declared count/digest guards | T10 |

M4 note: removing the empty check makes `_assemble` raise `IndexError` on
`keys[0]`, the connection drops, and the battery's `get()` helper does not catch
`RemoteDisconnected`, so the run aborts with a traceback instead of a FAIL line.
The mutant is killed, just noisily. This is a property of the mutant, not the
candidate: the candidate returns a clean `400 empty session: no slices stored`
(P8 below, and DEPLOY.md step 7).

### 2. Parity 10/10 with exactly one declared difference — PASS

Suite result: 10/10, exit 0. The one difference is the declared one —
`post` with an empty b64 body: v5 relays it upstream (`404 {"error":"API route
not ...`), v6 answers `400 empty body` locally.

The suite covers 8 read routes, so I checked the rest of the read surface myself
rather than take its case list as the boundary. A direct diff of the read-route
code (v5 lines 113-157 against v6 lines 332-378) shows three changed lines total:

- `post`: `+ "=="` becomes `_b64pad(...)`
- `post`: the new empty-body refusal (the declared difference)
- `agentfile`: `+ "=="` becomes `_b64pad(...)`

`get`, `agentbundle`, `agentlist` are byte-identical. The two `_b64pad` edits are
the only untested behaviour change, so I tested them directly across every b64
length class and on the routes the parity suite omits (probe2 sections A and B):

```
A0 agentfile b64len%4==0   v5=200/'content of fff.md'  v6=200/'content of fff.md'
A2 agentfile b64len%4==2   v5=200/'content of f.md'    v6=200/'content of f.md'
A3 agentfile b64len%4==3   v5=200/'content of ff.md'   v6=200/'content of ff.md'
B agentbundle/<uuid>       v5=200/104b  v6=200/104b
B agentlist/<uuid>         v5=200/27b   v6=200/27b
B agentbundle/nosuchuuid   v5=404/19b   v6=404/19b
B agentbundle/..           v5=400/8b    v6=400/8b
B agentfile /etc/passwd    v5=403/11b   v6=403/11b
B agentfile not-b64        v5=400/11b   v6=400/11b
```

Identical on all nine. No read-route regression.

Disclosure so "exactly one difference" is not left unqualified: `ping` also
changes, `bridge ok v5` to `bridge ok v6`. The parity suite does not compare ping
bodies. It is intended and DEPLOY.md step 5 smoke-tests for it, so I count it as
declared rather than as a regression, but it is a second observable difference.

### 3. Independent confirmation, not relying on the battery — PASS

Run out-of-process: the candidate launched as a real subprocess the way systemd
would launch it, driven by `curl`, with upstream POSTs counted from a log written
by a **separate** process (`stub_upstream.py`). No shared code with the battery.
Full transcript in `probe.out`.

**A session with a gap does not reach upstream.** Slices 0-11 minus seq 2:

```
wstat: {"n": 11, "seqs": [0,1,3,4,5,6,7,8,9,10,11], "assembles": false,
        "assembly_error": "non-contiguous slices: have 11 seq 0..11, missing [2]",
        "body_bytes": null}
wgo  : non-contiguous slices ... [HTTP 400]
upstream POSTs added: 0
```

Then filling seq 2 and committing honestly: 1 POST, body sha12 `073b400317b8`,
matching the sha256 of the payload I encoded. A gap that is not at the start
(seqs 0, 1, 9) is refused the same way, 0 POSTs (P5).

**A second commit on a committed session does not reach upstream.** Four separate
replay shapes against the committed sid:

```
replay wgo (same path)   409 {"error": "already committed", "upstream_status": 201, ...}
replay wgo (other path)  409
replay declared form     409
wput onto committed sid  409 session ... already committed; start a new one with /wnew
wshow (no re-POST)       201 {"ok": true, "bytes": 355}
upstream POSTs added: 0
```

`wshow` returns the stored 201 without re-POSTing, which is the point of it.

**Concurrent commits reach upstream exactly once.** Twelve simultaneous `wgo`
against one session:

```
12 concurrent wgo -> codes: 1x201, 11x409
upstream POSTs added: 1
```

The count is stronger than any single check: across the whole probe run the stub
recorded **4** POSTs total, and exactly 4 commits were legitimate (P1 honest, P3
race, P6, P7 honest). Every one carried body sha12 `073b400317b8`. No fragment,
no empty body, no duplicate.

I also confirmed the declared-count and declared-digest guard actually binds
(P7): wrong `n` -> 409, wrong sha -> 409, 0 POSTs, then the honest declared
commit -> 201 with exactly 1 POST. And that a client numbering slices from 1
instead of 0 still works (P6, 1 POST, correct hash).

### 4. `wput` reply shape — PASS

```
reply 1: 'ok 1 sid=bpSUvpQLasC6MhR_5_KElg seq=0'
reply 2: 'ok 2 sid=bpSUvpQLasC6MhR_5_KElg seq=1'
```

Leads with `ok <n>`; a parser splitting on whitespace still reads `$1=ok`,
`$2=2`. v5's reply was `ok <n>` exactly, so v6 only appends. No client in this
filesystem parses the reply — the caller is the Cowork side — so the criterion
rests on the shape itself, which holds.

### 5. Audit log content — PASS

91 lines from the independent run (`probe-audit.log`), covering
`start`, `wnew`, `wput`, `wgo`, `wshow`.

```
verb=wput sid=yhDXDe1T seq=0 n=1 chunk_len=40 rewrite=0
verb=wgo  sid=yhDXDe1T n=12 body_bytes=355 body_sha=073b400317b8 path=api/companies/X/issues declared=0
verb=wgo  sid=yhDXDe1T result=replay_refused prior_status=201
verb=wshow sid=yhDXDe1T status=201
```

sid prefixes, slice numbers, counts, byte lengths and body hashes are all
present. Leak checks:

- Body content: grep for the payload markers (`QA-23965`, `independent probe`,
  `zzzz`, `title`, `description`, and the literal chunk values) returns **0 hits**.
- Full sid: every `sid=` field in the log is 8 characters. Zero fields are longer.
- Cross-check: each of the four full 22-character sids observed in the probe
  transcript appears **0 times** in the audit log.

Two accuracy notes, neither blocking. The module docstring says "every w-verb
writes one audit line"; `wstat` writes none (it is read-only, so this is
defensible — the docstring is what is imprecise). Similarly the docstring says
sessions expire on every w-verb; `wshow` does not call `_wgc()`. The reaper
thread covers it within 60s.

I also confirmed a failed audit write cannot break a request: pointing
`AUDIT_PATH` at an unwritable location still gives `wnew -> 200` and
`wput -> 200 'ok 1 sid=... seq=0'`, with the line falling back to stderr.

### 6. `BRIDGE_TOKEN` unchanged — PASS

Candidate line 40 is `TOKEN = os.environ["BRIDGE_TOKEN"]`, identical to baseline
line 21. The comparison changes from `!=` to `secrets.compare_digest` with a
`try/except` wrapper, which is a hardening of *how* the token is compared, not a
change to the token or its source. `DEPLOY.md` line 19-20 and the ADR both state
no rotation. The unit file is untouched by the runbook.

### 7. `DEPLOY.md` walkthrough — PASS

Every step checks out against the live host:

- **Step 3**, `install -d -m 0750 -o root -g root /var/log/paperclip-bridge`: the
  unit has no `User=` directive and the running process is owned by root, so the
  bridge can write a root-only log directory. Correct. (If the step were skipped,
  `_audit` self-creates the directory via `os.makedirs`, so it fails safe either
  way.)
- **Step 5**, `grep -oP 'BRIDGE_TOKEN=\K\S+' ...`: extracts the value cleanly from
  the unit's `Environment=BRIDGE_TOKEN=...` line. Verified without printing it.
- **Step 7** is the one claim I checked byte-for-byte, because it is what an
  operator will compare against. Runbook says `400 empty session: no slices
  stored; GET /wstat/<sid> to inspect`. Observed:
  `400 empty session: no slices stored; GET /wstat/7dQkM4CWbioAkBvgh9LPwQ to inspect`.
  Then `wstat` -> `{"n": 0, "seqs": [], "assembles": false, "committed": false}`,
  matching "n=0, assembles=false". The claim that step 7 creates no issue and
  touches no data is true: the commit is refused before any relay.
- **Rollback** is a copy plus a restart, single file, no state on disk, no schema.
  Corroborated by `/opt/paperclip-bridge/bridge.py.backup.20260808T205753Z`,
  which shows the pattern is already in use on this host.
- The restart caveat is stated accurately: in-memory sessions drop and the next
  `wput`/`wgo` gets `404 no session`, which is loud and restartable.

## Finding — recorded, not blocking

**F1. Unhandled `ValueError` in the v6 declared-form parser drops the connection.**
Severity: low. Outside the acceptance scope. New code in v6.

Line 274 gates the declared form on `bits[0].isdigit()`, then line 275 calls
`int(bits[0])`. `str.isdigit()` returns True for characters `int()` rejects — the
superscripts U+00B2, U+00B3, U+00B9. `BaseHTTPRequestHandler` decodes the request
line as iso-8859-1, so a raw `0xB2` byte arrives in `self.path` as U+00B2 and the
`int()` raises.

Repro (`probe3.py`), against a stub upstream:

```
raw socket: GET /<token>/wgo/<sid>/\xb2\xb2/abcdefabcdef/api/x HTTP/1.1
expected:   a 4xx status
actual:     no response, connection closed; ValueError at bridge.py:275 in the log
control:    GET /<token>/wgo/<sid>/1/<sha12>/api/x  ->  HTTP/1.0 201 Created
```

Reproduces identically for `\xb3` and `\xb9`. `curl` and `urllib` percent-encode
these, and the bridge never percent-decodes the path, so no ordinary client can
reach it — the percent-encoded form is not treated as the declared form at all
and simply relays as a path segment.

Why this does not block: the caller must already hold a valid `BRIDGE_TOKEN` and
reach a loopback-only listener with a raw socket. The failure is fail-closed — 0
upstream POSTs — and it happens before `in_flight` is claimed, so the session is
left clean and usable (`in_flight=False`, still present). v5 in the same
situation answers 201 and relays a POST, so v6 is strictly safer here too. The
cost is one dropped connection and a traceback in the journal.

Suggested fix for a follow-up, at the CTO's discretion: move `int(bits[0])` into
the guard, e.g. gate on `bits[0].isascii() and bits[0].isdigit()`, or wrap the
parse in `try/except ValueError` and fall through to the plain form.

## Also checked, all clean

Hostile and malformed input to every w-verb returns a status, never a crash:
negative seq (`ok 2 ... seq=-5`), 300-digit seq, float seq (`400 bad seq`),
unknown sid on `wstat`/`wshow` (`404 no session`), `wgo` with no path
(`400 need sid/api-path`), empty verb (`404 unknown verb`), wrong token and
non-ASCII token (both `403 forbidden`). The session cap refuses past
`BRIDGE_WMAX_SESSIONS` with `503`, not a crash.

## Evidence in this directory

| file | what it is |
|---|---|
| `probe.sh`, `probe.out` | independent curl-driven confirmation of criterion 3 (P1-P8) |
| `stub_upstream.py` | out-of-process POST counter used by `probe.sh` |
| `probe2.py` | read-route parity beyond the shipped suite, audit-failure, hostile input, session cap |
| `probe3.py` | isolated stub-upstream repro of F1, with the v5 comparison |
| `mutants.sh` | the nine battery mutants |
| `probe-audit.log` | the 91-line audit log the leak checks were run against |
