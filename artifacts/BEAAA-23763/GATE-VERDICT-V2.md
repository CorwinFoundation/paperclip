# BEAAA-23763 — QA gate verdict on V2: **APPROVED**

QA agent cd1e82a0, 2026-08-16, host `AriClaw`. Candidate: BEAAA-23759 V2, commit
`c06e7ef25d4df9108dd95bc48ad1e7560f7f59fc`. Supersedes V1 `1f02da06`, which remains
**rejected** and must not be installed.

Approval covers **installation of the helper and merge of the source commit only**. It is
not authorization to arm, dispatch, deploy, or cut over. Install belongs to BEAAA-23764.

Nothing was installed, restarted, armed, dispatched, or deployed during this gate. No
candidate byte was edited. All execution ran in throwaway containers with isolated
filesystems and dummy values; the host helper is still `927fca8b…`, unmodified.

## 1. Exact hashes — all recomputed, all match

| Artifact | SHA-256 |
|---|---|
| focused exact-commit source archive | `16364f690b634e590c1e154111058b9eee5097c1aacf849ffff0adbb8737b77a` |
| git format-patch | `b78e506488f35a4563e6a524cc9e11c80d6120951d9ea07ed7c55c171479a055` |
| `backbond-real-agent-demo-secret-preflight` (V2, to install) | `9ecc60538c9904a2e9c1583c7af2ffed9d37f9775fa3888b02313237a53874e2` |
| `…secret-preflight.baseline-927fca8b` (rollback bytes) | `927fca8b796e7e868a319ab2fffb4372e34bb29228723090f31f6b994a46f6f8` |
| `install-helper.sh` | `12ac6c347cd5c802ee78db537128ce86acdf43deb9bd78a5ec7e2f1fa50f450b` |
| `rollback-helper.sh` | `873fde957ea021e863d07586e4d9b959f235c50364834a351fbcd5fc4a48fd26` |
| `README.md` | `b27429cb082899a0fe3ef966ffa7785abfacd433f04a6cc74e9d2631ab75ef4e` |
| `demo-real-agent/deploy.sh` (at `c06e7ef2`) | `77f979d237a0ac9a9250f465e67f8ccc09a4e2ece2100570986cefad13ed38f0` |
| `demo-real-agent/tests/test_deploy_judge_socket_beaaa23759_v2.py` | `3922539acb9db21dfa67726e341a0cb3ab1d3bf14ffe531fe55e848cee5a89c8` |

Inner `SHA256SUMS` verifies (5/5 OK). Every one of the 8 files in the archive is
**byte-identical** to its blob at commit `c06e7ef2` (`git cat-file blob` compared to
`sha256sum`). The commit adds exactly the 7 new files and modifies only `deploy.sh`.

**The shipped rollback bytes are byte-identical to the helper currently installed on
this host.** That is the single most important integrity check here, and it passes:
`sha256sum /usr/local/sbin/backbond-real-agent-demo-secret-preflight` →
`927fca8b796e7e868a319ab2fffb4372e34bb29228723090f31f6b994a46f6f8`.

## 2. Ancestry — verified independently

The producer flagged that their authenticated fetch failed and asked QA to revalidate.
Done:

```
c06e7ef2  parent = d9d680f53db7de263f026be5284c97229126872c
origin/master     = d9d680f53db7de263f026be5284c97229126872c
commits on master not in candidate: 0
commits in candidate not on master: 1
```

The parent is exactly current `origin/master`, which is also the release SHA the failed
one-shot run deployed. Zero behind, one ahead, single commit. No stale-base risk.

## 3. Privilege — grants nothing

The candidate adds **no** group, ACL, socket permission, sudoers entry, SSH permission,
mount, container user, service permission, or marker. Re-confirmed on the host after the
gate: `backbond-judge-client` absent; `/run/backbond-claude-judge` still
`drwxr-x--- root backbond-demo`; `passive-demo-deploy` still
`passive-demo-deploy,docker`.

`install-helper.sh` writes exactly one path,
`/usr/local/sbin/backbond-real-agent-demo-secret-preflight`, as `root:root 0755`, and
touches nothing else. Readiness is decided by a root-side `lstat` inside the existing
fixed, zero-argument helper, reached through the one existing sudoers rule. The deploy
principal gains no new reach whatsoever — it only reads one additional names-only line
of stdout.

This is strictly better than the mandate, which asked for "only the minimum judge-socket
client access." The minimum turned out to be none.

## 4. Restart and host-reboot durability — structurally trivial

There is nothing to persist: no runtime-directory ACL, no tmpfiles entry, no drop-in, no
group membership. `judge_socket_status()` is a root `lstat` evaluated fresh on each
invocation, so it is unaffected by `systemd` runtime-directory recreation, by
`RuntimeDirectoryMode=0750`, and by `claude_judge_proxy.py`'s `os.chmod(0o750/0o660)`
calls on every start — the three mechanisms that defeated V1. No restart of
`backbond-claude-judge.service` is required to install, and none is performed.

## 5. Fail-closed matrix — executed end-to-end, not asserted

The shipped tests (12 passed) exercise `judge_socket_status()` against real socket and
non-socket paths and execute the extracted `parse_judge_socket_status()` bash function
against crafted payloads. They do **not** run `deploy.sh` end-to-end, so I did, using
stub `sudo`/`docker`/`curl` on `PATH` — `docker` logs its argv and fails, so "reached
Docker" is observable:

| Case | Result | Docker reached |
|---|---|---|
| **Drift: V2 `deploy.sh` + the helper installed on this host today** (true captured stdout, no `JUDGE_SOCKET` line) | exit 1, "malformed judge-socket status" | **NO** |
| healthy → `JUDGE_SOCKET: ready` | proceeds to build | YES (correct) |
| service down → `JUDGE_SOCKET: missing` | exit 1, "socket is not ready" | **NO** |
| contradictory → `ready` **and** `missing` | exit 1, "malformed" | **NO** |
| spoof → `JUDGE_SOCKET: ready extra` | exit 1, "malformed" | **NO** |
| helper exits non-zero | exit 1, "preflight evaluation failed" | **NO** |
| replay with nothing armed | `deploy_authorization: absent`, non-zero | **NO** |

The drift case matters most for sequencing: merging the source commit **without**
installing the V2 helper fails closed before Docker. Source and host can therefore be
landed in either order without risking a partial deploy.

## 6. Secret and boundary non-disclosure

Helper output remains names-only. `judge_socket_status()` returns the literal strings
`ready`/`missing` and never a path, mode, owner, or payload. `/etc/backbond` stays
`drwx------ root root`; `real-agent-demo.env` stays unreadable to uid 1002 and to me.
The `validate()`, `_read_fixed_file()`, `_read_authorization()` and
`consume_authorization()` paths are byte-identical to the installed baseline — verified
by `diff`, which shows exactly three additions: one constant, one 11-line function, and
a 7-line block in `main()`.

## 7. Finding — inherited, not introduced: an unready socket still spends the one-shot grant

`main()` prints `JUDGE_SOCKET: missing` and then calls `consume_authorization()`
unconditionally. Demonstrated in a root sandbox with dummy values:

```
SCENARIO 1: judge socket ABSENT
  before: .allow PRESENT | .consumed absent
  JUDGE_SOCKET: missing
  deploy_authorization: consumed d9d680f53db7de263f026be5284c97229126872c
  AFTER:  .allow absent  | .consumed PRESENT      <-- grant spent, no deploy
```

`deploy.sh` then exits 1 without reaching Docker. The deployment is correctly refused,
but the Founder-gated one-shot authorization has already been renamed to `.consumed`, so
a retry needs a fresh Founder decision.

This is **not a regression**: the helper installed today consumes just as
unconditionally, and the same burn happened on the original failed run. It is also
strictly fail-closed. It is not in this candidate's changed surface, and V2 correctly
declined to rewrite inherited authorization logic. So it does not block approval — but
it must be managed operationally and fixed separately:

- **Mandatory pre-arm condition:** before creating
  `/etc/backbond/real-agent-demo-deploy.allow`, confirm the judge socket is listening
  (`ss -lx | grep judge.sock` shows LISTEN and
  `systemctl is-active backbond-claude-judge.service` is `active`). Arming while the
  service is down destroys the grant for nothing.
- **Follow-up fix:** move the readiness decision ahead of consumption — return non-zero
  on `missing` *without* renaming — so an infrastructure outage cannot spend a
  Founder-gated authorization.

## 8. Install, restart, and rollback procedure (as approved)

Install, as root, under BEAAA-23764 only:

```sh
sudo ./install-helper.sh <candidate-dir>
```

It refuses unless the live target is exactly `927fca8b…` `root:root 0755` and the
candidate is exactly `9ecc6053…`; stages via `mktemp` in the same directory, re-verifies
the staged hash, then `mv -f` (atomic, same filesystem). **No service restart is
required or performed.** The judge service, the live container, sudoers, SSH and Docker
are untouched.

Post-install expectation: `sha256sum` of the target is `9ecc6053…`, owner `root:root`,
mode `0755`.

Rollback, as root:

```sh
sudo ./rollback-helper.sh <candidate-dir>
```

Symmetric and equally hash-gated: refuses unless the live target is exactly `9ecc6053…`
and the shipped baseline is exactly `927fca8b…`, then atomically restores. Unlike V1's
rollback, this one cannot silently succeed while leaving state behind — every step is a
`test` under `set -eu`.

Unit / drop-in / tmpfiles / group / ACL semantics: **none — this candidate introduces no
unit, no drop-in, no tmpfiles entry, no group and no ACL.** That is the substantive
difference from V1.

## 9. Boundary state at close — unchanged

- Live container `f4938b7e4adb` `backbond/passive-monitor-demo:dcea859e…` — healthy, 11 days.
- `/etc/backbond/real-agent-demo-deploy.allow` — absent. `…deploy.consumed` — absent.
- `/var/lib/backbond-real-agent-demo/d9-one-shot.armed` and `.consumed` — absent.
- Host helper still `927fca8b…` (this gate installed nothing).
- `backbond-claude-judge.service` active, `judge.sock` LISTEN.
- `backbond-judge-client` group absent; `/run/backbond-claude-judge` unchanged.

## Verdict

**APPROVED** — commit `c06e7ef25d4df9108dd95bc48ad1e7560f7f59fc`, helper
`9ecc60538c9904a2e9c1583c7af2ffed9d37f9775fa3888b02313237a53874e2`, rollback
`927fca8b796e7e868a319ab2fffb4372e34bb29228723090f31f6b994a46f6f8`, owner `root:root`,
mode `0755`, path `/usr/local/sbin/backbond-real-agent-demo-secret-preflight`.

Install under BEAAA-23764 only, subject to the pre-arm condition in §7. CTO co-sign
remains required before merge per the producer's own classification. No arming, no
dispatch, no deploy is authorized by this approval.
