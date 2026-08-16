# BEAAA-23768 — QA verdict on candidate `b2d1e480`

**Verdict: CHANGES REQUESTED**

- Candidate: `b2d1e480b9943e9eb917c03c07187e99307e4cd9` (branch `agent/beaaa-23760-judge-readiness`)
- Tree: `e7eab31685b5144576a54f7e45283da306a6a3ac` — matches the producer's claim exactly
- Parent: `f61907c17ace1c69365a28983cf38686f9c1c055` (the superseded candidate)
- Base: `d9d680f53db7de263f026be5284c97229126872c` — confirmed ancestor
- Supersedes: `f61907c17ace…` → `b2d1e480…`
- Date: 2026-08-16

Nothing was installed, deployed, armed, restarted, or altered on the host. No ACL, group,
repository-variable, workflow, or container action was taken. `sudo` requires a password at this
seat, so no root state was reachable. The installed helper was read, never executed — executing it
consumes a live Founder one-shot grant.

## What the candidate gets right

The revision fixes the grant-burn defect that blocked `f61907c17ace`. `main()` now orders the work
`validate_state_dir → validate_authorization → judge_socket_readiness → (only if ready)
consume_authorization`, so a not-ready socket returns 1 with the grant intact.

| Required evidence | Result |
|---|---|
| Ready socket | PASS — 4 valid schema variants proceed to Docker |
| Missing socket | PASS — helper exits 1, deploy exits 1, **0 Docker calls** |
| Malformed result | PASS — 8 malformed variants all blocked, **0 Docker calls** each |
| Helper failure | PASS — non-zero helper exit blocked before any Docker call |
| Grant preserved on every failure class | PASS — mutations M4 and M5b both killed |
| Non-socket inode rejected | PASS — `lstat` + `S_ISSOCK`; M2/M3 killed |
| uid 1002 not in gid 10001 | PASS — `passive-demo-deploy` groups are `1002`, `987(docker)`; `backbond-demo` member list empty |
| uid 1002 cannot connect directly | PASS — `EACCES` on both `lstat` and `connect` |
| No secret values emitted | PASS (helper) — sentinel values never appear; names only |
| Canonical-master ancestor `d9d680f5` | PASS |
| Focused regressions | PASS — 55/55 across `demo-real-agent/tests/` |
| N=5 delta zero | PASS by construction — all 8 scoring-surface trees byte-identical |
| Socket remains `0660 root:backbond-demo` | **NOT OBSERVABLE** — see caveat below |

### N=5 delta

Proven by tree identity rather than by running the harness. Every scoring surface
(`backbond`, `corpus`, `config`, `contracts`, `api`, `ci_plugin`, `cadence`, `tools`) has an
identical tree object at `d9d680f5` and `b2d1e480`. The complete changed-path set is three files
under `demo-real-agent/`, none of which participate in scoring. Delta is zero by construction.

### Socket mode caveat

`/run/backbond-claude-judge` is `0750 root:backbond-demo`, so the socket's own mode and owner are
unreadable from any non-root seat (`statx` → `EACCES`). I can state only that **this candidate does
not change them**: the production diff adds no `chmod`, `chown`, `mkdir`, or `setfacl`. The two
`mkdir`/`chmod` additions in the diff are `tmp_path` test fixtures. The socket is live —
`backbond-claude-judge.service` is active and `/run/backbond-claude-judge/judge.sock` is LISTENing.

## Blocking findings

### F1 — the deploy binds a caller-controlled directory while root attests a fixed one

`deploy.sh:14` still resolves the bind source from the environment:

    JUDGE_SOCKET_DIR="${CLAUDE_JUDGE_SOCKET_DIR:-/run/backbond-claude-judge}"

The helper hardcodes `JUDGE_SOCKET_PATH = /run/backbond-claude-judge/judge.sock`. So the root-evaluated
verdict describes one path while the container receives another. Reproduced end to end: with an
unmodified `deploy.sh` and a green `JUDGE_SOCKET: ready` verdict, the container was handed

    type=bind,source=/tmp/qa23768v2/attacker-socket-dir,target=/run/claude-judge,readonly

The whole point of this ticket is a trustworthy readiness verdict, and the verdict does not describe
the thing that gets mounted.

Two qualifications, stated so the CTO can scope this correctly:

1. **This is pre-existing, not introduced by the candidate.** The env indirection is already on
   master. The candidate does not make it worse; it also does not fix it while making the verdict
   authoritative.
2. **It is not a novel privilege escalation.** uid 1002 is in group `987(docker)` and therefore
   already has host-root-equivalent power. The real cost is a broken attestation and an operator
   footgun: a stale `CLAUDE_JUDGE_SOCKET_DIR` yields a green root verdict over a wrong mount.

`test_deploy_uses_only_fixed_preflight_and_fixed_bind_mount` does not cover this despite its name —
it is entirely source-substring greps, and the only bind-mount assertion it makes is about
`ENV_FILE`. This is the same defect recorded as D2 on gate BEAAA-23766.

### F2 — helper output contract collides with the helper approved in BEAAA-23763

The helper approved for install by the parallel gate BEAAA-23763 V2 (`c06e7ef2`, file sha256
`9ecc60538c9904a2e9c1583c7af2ffed9d37f9775fa3888b02313237a53874e2`) emits a `JUDGE_SOCKET:` line
**only when `DEMO_JUDGE_BACKEND` is configured**:

    if "DEMO_JUDGE_BACKEND" in configured:
        ...
        print(f"JUDGE_SOCKET: {socket_status}")

This candidate's `deploy.sh` requires `JUDGE_SOCKET: ready` in **all four** accepted schemas,
including the OpenRouter, Anthropic and both-key variants. Replaying that approved helper's exact
stdout through the unmodified candidate `deploy.sh`:

| Backend configuration | Result |
|---|---|
| proxy (`DEMO_JUDGE_BACKEND`) | PROCEEDED — schemas agree |
| OpenRouter / Anthropic (no proxy) | **BLOCKED — "malformed preflight result", 0 Docker calls** |

In the blocked case the grant has already been renamed to `consumed` by that helper before its final
print, so the outcome is a burned Founder one-shot **and** a dead-ended deploy. Which branch applies
depends on `/etc/backbond/real-agent-demo.env`, which is `0600` and unreadable at any non-root seat,
so the failing path cannot be ruled out from here.

### F3 — the helper installed on this host today is still incompatible (carry-over)

`/usr/local/sbin/backbond-real-agent-demo-secret-preflight` is unchanged at sha256
`927fca8b796e7e868a319ab2fffb4372e34bb29228723090f31f6b994a46f6f8`. It emits no `JUDGE_SOCKET` line
at all (`grep -c` → 0) and calls `consume_authorization()` unconditionally before its final print.
Replaying its exact stdout: **BLOCKED, 0 Docker calls**, grant burned. Landing this `deploy.sh`
before a compatible helper is installed dead-ends the deploy and spends the grant.

## Non-blocking findings

- **F4 — raw helper stdout is echoed before the schema is checked.** `printf '%s\n' "$PREFLIGHT_STATUS"`
  runs before the `case`. An injected trailing `DEMO_ACCESS_TOKEN=s3cr3t` line appeared on deploy
  stdout and only then was the output rejected. The shipped helper is names-only by construction, so
  there is no active leak; validate before echoing as defence in depth.
- **F5 — coverage gap.** Making `validate_authorization()` an unconditional early `return` leaves all
  28 tests passing. The grant's owner, mode, size and release-SHA checks are unverified by this suite.
- **F6 — test count does not reconcile.** The producer reports "36 focused tests passed". The named
  module collects **28**; the whole `demo-real-agent/tests/` directory is **55**; adding the other
  ticket's 6-test file gives 34. I could not reproduce 36 under any grouping.
- **Scope observation.** The readiness gate is now unconditional for every backend, where master
  applied it only to the proxy backend. This is fail-closed and defensible, but it means a
  non-proxy deployment cannot proceed if the judge service ever stops. Not currently blocking — the
  service is active and the socket is listening.

## Method

- Stubbed `sudo`, `docker` and `curl` on `PATH` and ran the **unmodified** candidate `deploy.sh`.
  The absolute-path `[[ -x $PREFLIGHT_HELPER ]]` check still passes against the real installed file
  while the stub intercepts execution. Counting Docker invocations gives PROCEEDED vs BLOCKED.
- 14 preflight-output scenarios, including replays of the real installed helper and of the
  BEAAA-23763 V2 approved helper.
- 7 mutations against the new suite. M1, M2, M3, M4, M5b, M6 killed; M7 survived. One first-pass
  mutant was malformed (syntax error) and was re-run correctly as M5b rather than counted.
- Helper restored byte-identical to the candidate blob after mutation
  (`2990a3309b7864c35ebf2002f351bb98012951c2a7001a1c2d213363f50000e4`).

## Unblock owner

Engineer `d8bbc3b5-185f-43ed-bff0-d00323ddbfa3`, with CTO co-sign on BEAAA-23767, and a CTO decision
on the F2 sequencing between BEAAA-23760 and BEAAA-23763 — the two tickets currently define
conflicting output contracts for the same root helper.
