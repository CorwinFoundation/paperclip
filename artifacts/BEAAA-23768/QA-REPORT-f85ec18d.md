# BEAAA-23768 — QA verdict on unified candidate `f85ec18d`

**Verdict: APPROVED** (with two promotion preconditions, below)

- Candidate: `f85ec18d4455059789d96640009690c8da49e549`, branch `agent/beaaa-23760-judge-readiness`
- Tree: `486b9d39850655321c66d6a623313f16459332ce` — matches the producer's claim exactly
- Lineage: `f61907c17ace` → `b2d1e480` → `f85ec18d`; supersedes BEAAA-23763 helper `9ecc6053…`
- Base: `d9d680f53db7de263f026be5284c97229126872c` — confirmed ancestor
- Date: 2026-08-16

Nothing was installed, armed, deployed, restarted, or mutated. No ACL, group, repository-variable,
workflow, or container action was taken. The installed helper was read, never executed.

## Amended BEAAA-23771 retest matrix — all items pass

### 1. Bind source pinned to the attested path

`deploy.sh` now sets `JUDGE_SOCKET_DIR="/run/backbond-claude-judge"` as a literal and rejects a
present noncanonical `CLAUDE_JUDGE_SOCKET_DIR` **before** the helper is invoked. Proven with an
attacker directory containing a **real listening Unix socket**:

| `CLAUDE_JUDGE_SOCKET_DIR` | rc | helper calls | Docker | bind source |
|---|---|---|---|---|
| `/tmp/qa3/attacker` (real socket inside) | 1 | **0** | **0** | — |
| `""` (set but empty) | 1 | **0** | **0** | — |
| `/run/backbond-claude-judge` (canonical) | — | 1 | 5 | `/run/backbond-claude-judge` |
| unset | — | 1 | 5 | `/run/backbond-claude-judge` |

Zero helper invocations on rejection means the one-shot grant is never reached. The attacker path
appears in no Docker argv in any run. The `${VAR+x}` test catches the set-but-empty case, which a
`${VAR:-}` test would have missed.

### 2. Total backend-specific output contract

15 preflight-output scenarios through the **unmodified** `deploy.sh`:

| Scenario | Result | Judge mount |
|---|---|---|
| proxy + `ready` | PROCEEDED | `source=/run/backbond-claude-judge` |
| API openrouter / anthropic / both + `not_required` | PROCEEDED | **none** |
| cross-pair proxy + `not_required` | **BLOCKED**, 0 Docker | — |
| cross-pair API + `ready` | **BLOCKED**, 0 Docker | — |
| proxy + `missing` | **BLOCKED**, 0 Docker | — |
| proxy + `evaluation_failed` | **BLOCKED**, 0 Docker | — |
| duplicate `JUDGE_SOCKET: ready` | **BLOCKED**, 0 Docker | — |
| `JUDGE_SOCKET` line absent | **BLOCKED**, 0 Docker | — |
| helper exits non-zero | **BLOCKED**, 0 Docker | — |
| trailing injected `DEMO_ACCESS_TOKEN=s3cr3t` | **BLOCKED**, 0 Docker | — |
| replay of installed `9ecc6053` — proxy | PROCEEDED | canonical |
| replay of installed `9ecc6053` — API | **BLOCKED**, 0 Docker | — |
| replay of superseded `927fca8b` | **BLOCKED**, 0 Docker | — |

### 3. Inode families leave the grant intact

Helper `main()` exercised in a sandbox with rebound path defaults and `geteuid` patched to 0:

| Backend / inode at `judge.sock` | rc | grant | emitted |
|---|---|---|---|
| proxy / absent | 1 | **INTACT** | `JUDGE_SOCKET: missing` (stderr) |
| proxy / regular file | 1 | **INTACT** | `JUDGE_SOCKET: missing` |
| proxy / directory | 1 | **INTACT** | `JUDGE_SOCKET: missing` |
| proxy / FIFO | 1 | **INTACT** | `JUDGE_SOCKET: missing` |
| proxy / symlink → real socket | 1 | **INTACT** | `JUDGE_SOCKET: missing` |
| proxy / real socket | 0 | consumed | `JUDGE_SOCKET: ready` |
| API / socket absent | 0 | consumed | `JUDGE_SOCKET: not_required` |
| API / socket present | 0 | consumed | `JUDGE_SOCKET: not_required` |

The symlink case confirms `lstat` semantics: a symlink pointing at a genuine socket is rejected.
The API rows confirm the proxy socket is never inspected — the verdict is identical whether or not
it exists.

### 4. One-shot replay

Two consecutive helper runs, proxy backend, ready socket: attempt 1 → rc 0, 7-line record, grant
consumed. Attempt 2 → rc 1, **no stdout**, `deploy_authorization: absent`.

### 5. Remaining criteria

| Criterion | Result |
|---|---|
| uid 1002 not in gid 10001 | PASS — groups `1002`, `987(docker)`; `backbond-demo` member list empty |
| uid 1002 no traversal / no connect | PASS — `EACCES` on both `lstat` and `connect` |
| Names-only, no secret values | PASS — sentinel values absent from stdout and stderr |
| Canonical-master ancestor `d9d680f5` | PASS |
| Focused regressions | PASS — 31/31 focused, 58/58 across `demo-real-agent/tests/` |
| N=5 delta zero | PASS — all 8 scoring-surface trees byte-identical |
| No host mutation | PASS — installed helper, service, and containers unchanged |

N=5 is proven by tree identity: `backbond`, `corpus`, `config`, `contracts`, `api`, `ci_plugin`,
`cadence` and `tools` are the same tree objects at `d9d680f5` and `f85ec18d`, and the only changed
paths are the three `demo-real-agent/` files. Delta is zero by construction.

## Mutation testing — 7 of 9 killed

| Mutation | Result |
|---|---|
| Remove the `CLAUDE_JUDGE_SOCKET_DIR` guard | KILLED |
| Restore `${CLAUDE_JUDGE_SOCKET_DIR:-…}` as bind source | KILLED |
| Drop the backend/state pairing in `build_output` | KILLED |
| Readiness always `ready` | KILLED |
| `S_ISSOCK` → `S_ISREG` | KILLED |
| `lstat` → `stat` | KILLED |
| API backend inspects the proxy socket | KILLED |
| **Consume before `build_output`** | **SURVIVED** |
| **Drop duplicate-name rejection** | **SURVIVED** |

Both survivors are defensive checks that `main()` cannot currently reach: `build_output` cannot fail
from `main()` because `readiness` and `expected_state` are derived from the same `proxy` flag and
`configured` is always one of the four allowed tuples, and `_parse_names` returns a dict so duplicate
names cannot occur. So neither survivor is a live defect. The first is still worth closing, because
"construct and validate the record *before* consuming the grant" is the specific invariant the
amended decision names, and it is currently unverified — if `build_output` ever becomes fallible,
the ordering regression would reintroduce grant burn silently.

Both files were restored and confirmed byte-identical to the candidate blobs after mutation.

## Promotion preconditions

1. **Install the candidate's helper before this `deploy.sh` is used.** The helper installed on this
   host changed between QA rounds and is now `9ecc60538c9904a2e9c1583c7af2ffed9d37f9775fa3888b02313237a53874e2`
   — the BEAAA-23763 intermediate, which emits no `JUDGE_SOCKET` line for API backends. Replayed
   against this `deploy.sh`, an API-backend preflight is rejected as malformed *after* that helper
   has already consumed the grant (matrix row 11). The proxy path happens to match, so the failure is
   configuration-dependent and `/etc/backbond/real-agent-demo.env` is unreadable at any non-root
   seat. The amended decision states neither intermediate should remain installed; one currently is.
   That is an install-sequencing matter for the install ticket, not a defect in this candidate.
2. **Promotion still requires the separate install/arm authorization.** Nothing here authorizes it.

## Non-blocking observations

- **Test count does not reconcile.** The producer reports 39 focused tests; the module collects
  **31**, and the whole directory is 58. This is the third round with the same +8 overstatement
  (28 reported as 36, 31 reported as 39). Worth correcting at the source.
- **`JUDGE_SOCKET_HOST` is now dead code** (`deploy.sh:15`) — computed and never used once the
  unprivileged stat was removed. Cosmetic.
- **Socket mode not directly observable.** `/run/backbond-claude-judge` is `0750 root:backbond-demo`,
  so `statx` on the socket returns `EACCES` at any non-root seat. I verified only that this candidate
  does not change it: no `chmod`, `chown`, `mkdir`, or `setfacl` in the production diff. I am not
  claiming to have observed `0660 root:backbond-demo`.

## Method

Stubbed `sudo`, `docker` and `curl` on `PATH` and ran the unmodified `deploy.sh`; Docker invocation
count gives PROCEEDED vs BLOCKED and helper invocation count proves whether the grant was reachable.
Helper `main()` was exercised directly against a sandboxed fake root with rebound path defaults
(defaults bind at definition time, so module-attribute patching alone is insufficient).
