# BEAAA-23763 — QA gate verdict: **STOP**

QA agent cd1e82a0, 2026-08-16, host `AriClaw`. Candidate for BEAAA-23759.

No candidate bytes were edited. Nothing was installed, no service restarted, no marker
armed, no GitHub variable changed, nothing dispatched or deployed. All destructive
mechanism testing ran against isolated scratch paths under `/tmp/qa23763` and a
throwaway container, never against `/run/backbond-claude-judge`.

## Hash verification — PASS

| Artifact | SHA-256 | Result |
|---|---|---|
| outer archive (`/api/attachments/64a62b35-890b-416a-8bd2-ddb004673c23/content`) | `1f02da064f96e4ec2ac111816cfce8300df9b4112dd3d6dff870c42dad2c880c` | matches claim |
| `00-backbond-judge-client.conf` | `8f1b4316a8fda8b7bf186ffd7b38a87a1540693beac6bde0b8b2f754c888ae6c` | OK |
| `10-backbond-claude-judge.conf` | `32f8cd6e5f67207342e3bb2f6c66922bf3542afaf0e836238cff250285a18093` | OK |
| `20-judge-client-runtime.conf` | `56b85115ab1ce039a86616cbb8364413c3f9d0f19fe6ff1a891f336d9161978c` | OK |
| `README.md` | `41d6c9f30b1b5774ba2e5f0f8e36e279e9518297cfced274612726fd8a2e28f6` | OK |
| `install.sh` | `0ae23d03c7f92194f3a15b7413ea1ed76a7ec5a967abead2d2191d5bdb33d139` | OK |
| `rollback.sh` | `081e8e98c7bf748bb672a84005f4da8e8c31bc7b62db5ba8d4bd540f626009aa` | OK |
| `verify-host.sh` | `e0db07583f46118cb9a7e0f5dd3729416acb37fb3626667d0cee4a74e1842225` | OK |

Manifest is complete: no unlisted files, no listed-but-missing files. Every hash was
recomputed from the delivered bytes. **Hashes are the only part of this candidate that
passes.**

## F1 — `setfacl` does not exist on the target host; install aborts mid-way. BLOCKER

`install.sh:15` calls `setfacl`. The `acl` package is not installed on this host
(`dpkg -l acl` → state `un`); `setfacl`, `getfacl` and `chacl` are all absent from
every standard directory. `libacl.so.1` is present and systemd 255 reports `+ACL`, so
`systemd-tmpfiles` works, but the binary the installer shells out to does not exist.

Replayed under a host-equivalent `PATH` (stubs that log and exit 0, `setfacl`
deliberately absent):

```
/tmp/.../install.sh: 15: setfacl: not found      EXIT=127
steps already executed before the abort:
  install -d ... /etc/systemd/system/backbond-claude-judge.service.d
  install ... /etc/sysusers.d/backbond-judge-client.conf
  install ... /etc/tmpfiles.d/backbond-claude-judge.conf
  install ... /etc/systemd/system/backbond-claude-judge.service.d/20-judge-client-runtime.conf
  systemd-sysusers /etc/sysusers.d/backbond-judge-client.conf
  usermod -a -G backbond-judge-client passive-demo-deploy
  systemd-tmpfiles --create /etc/tmpfiles.d/backbond-claude-judge.conf
```

`set -eu` aborts at line 15, so `systemctl daemon-reload` (line 16) never runs. The
host is left with three config files, a new group, a new supplementary membership on
`passive-demo-deploy`, and ACL entries applied to the live `/run/backbond-claude-judge`
— and no socket access, so the deploy still fails. Partial install, no automatic
rollback.

## F2 — `rollback.sh` reports success while leaving the grant in place. BLOCKER

Both ACL-removal lines are `setfacl ... 2>/dev/null || true`. With `setfacl` absent
they are silent no-ops, and nothing re-runs `systemd-tmpfiles`. Replayed under the same
PATH:

```
EXIT=0
steps executed: gpasswd -d ...; rm -f (x3); systemctl daemon-reload
```

Exit 0 on a security rollback, while `g:backbond-judge-client:--x` remains on the
production runtime directory and the group is retained by design (`rollback.sh:13-15`).
An operator following the documented procedure is told the grant is gone when it is
not. A rollback that cannot fail is not a rollback.

## F3 — the durable mechanism does not work: `A+` creates no default ACL. BLOCKER

This is the centre of the design. README §31 claims "A newly created `judge.sock`
inherits `rw-` for the dedicated group." It does not, on this host.

Applied the candidate's own tmpfiles lines verbatim (paths and owner/group rewritten to
an isolated scratch dir; the `a+`/`A+` entries byte-identical in form), then bound a
Unix socket exactly as `claude_judge_proxy.py` does:

```
systemd-tmpfiles --create ...            EXIT=0, no warnings

DIR   mode=770
  posix_acl_access:  user_obj=rwx  group_obj=r-x  group:1001=--x  mask=rwx  other=---
  posix_acl_default: ABSENT            <-- A+ produced nothing

SOCKET after bind()  mode=755
  posix_acl_access:  ABSENT            <-- nothing inherited

AFTER proxy chmod(dir 0750, sock 0660):
  dir mode=750, sock mode=660
  dir  posix_acl_access: group:1001=--x, mask=r-x   (traverse survives)
  sock posix_acl_access: ABSENT
```

`systemd-tmpfiles` accepted the `A+` line and exited 0 without creating a default ACL.
Consequently a socket created afterwards inherits nothing. The *only* thing that would
ever grant socket access is the one-shot `setfacl` at `install.sh:15` against the
already-live socket — which is precisely the "ad-hoc chmod/chown of a transient socket"
that BEAAA-23759's own acceptance forbids, and which is lost the next time the socket
is recreated (service restart or host boot). The candidate fails the durability
requirement it was written to satisfy.

The directory traverse entry (`a+`) does survive the proxy's `os.chmod(dir, 0o750)`:
mask drops to `r-x`, and `--x ∩ r-x = --x`. That half is durable. It is also the only
half that is needed — see F4.

## F4 — the socket grant is unnecessary over-privilege. BLOCKER

The candidate exists to make the deploy user's `[[ -S "$JUDGE_SOCKET_HOST" ]]` succeed.
`stat()` needs only search permission on the parent directory — no permission at all on
the socket inode. Verified in an isolated container with equivalent POSIX modes
(uid 1002 `tester`, not in `demo`):

```
A) dir root:demo 0750, socket root:demo 0660   -> test -S => FALSE   (today's failure)
B) dir grants traverse-only, socket UNCHANGED  -> test -S => TRUE
                                                  ls dir   => Permission denied
                                                  connect() => DENIED (EACCES)
```

Traverse alone fixes the readiness check. The candidate additionally grants `rw-` on
`judge.sock`, which is exactly the difference between `connect() => DENIED` and
`connect() => SUCCEEDED` against the Claude-Max-backed judge bridge. `claude_judge_proxy.py`
still checks a per-request token (`hmac.compare_digest` against
`CLAUDE_JUDGE_PROXY_TOKEN`), so this is not by itself sufficient to issue judge calls —
but it removes one of two independent barriers and adds a connect/exhaust surface
against a live service, for no functional gain.

## F5 — the verifier was never executed against the target host. BLOCKER

`verify-host.sh:20` and `:33` both require `/usr/local/sbin/backbond-real-agent-demo-deploy`.
That path does not exist. The only installed helpers are:

```
-rwxr-xr-x 1 root root  7777 Aug 16 16:42 backbond-real-agent-demo-secret-preflight
-rwxr-xr-x 1 root root 19837 Aug 16 16:42 backbond-real-agent-demo-ssh-gate
```

So `fail fixed_helper_missing` and `fail pinned_client_preflight` would both fire. The
candidate ships a verifier that cannot pass on its target. README §14 concedes the
inventory is "based on the incident evidence supplied with the issue" — the package was
authored without observing the host.

## F6 — advisory

Immediately after `systemd-tmpfiles`, the directory's `stat -c %a` reads **770**, not
750, because the ACL mask (`rwx`) occupies the group position. Effective access is not
widened — a mask caps, it never grants — but `verify-host.sh:12` asserts the exact
string `root:backbond-demo 750`, so that assertion depends on the proxy's `chmod`
having already run. Separately, `systemd-tmpfiles` exits **0** while printing
`Failed to parse ACL "g:backbond-judge-client:--x", ignoring` when the group is
unresolvable, so a boot-order or packaging regression would silently produce an
ungranted directory with a success exit.

## The comparison the board asked for

| | root-preflight-helper route | this group/ACL candidate |
|---|---|---|
| New host access for `passive-demo-deploy` | none | new group, dir traverse, socket `rw-` |
| Installs on this host today | yes | no (F1) |
| Durable across restart/boot | yes (root-side `lstat`, no permissions involved) | no (F3) |
| Clean rollback | n/a — nothing granted | no (F2) |
| Status | written and **already QA-approved** | fails five ways |

The zero-privilege route is not hypothetical. The working-tree
`demo-real-agent/deploy.sh` already drops the direct `-S` test and reads
`JUDGE_SOCKET: ready|missing` from the root helper; the helper half is
`backbond-real-agent-demo-secret-preflight` SHA-256
`3dfc576598c74c54155cf211b46927ea490b5c8f0e67560af01d4cb4f01b49cf`, inside candidate
`b53d08b3`, approved on BEAAA-23704, install authorized under BEAAA-23705 and not yet
performed. The host still carries the older `927fca8b79…`, which emits no
`JUDGE_SOCKET:` line at all.

**Verdict: the group/ACL mechanism is not uniquely necessary, and on this host it does
not install, does not durably grant, and does not roll back.** Issue acceptance forbids
APPROVED on any one of those. STOP.

## If the CTO still wants a host-side grant

Minimum corrections, none of which change the F4 conclusion that this route grants more
than zero:

1. Delete the `A+` line and `install.sh:15`. Keep only `a+ … g:backbond-judge-client:--x`.
   Traverse is sufficient (F4) and durable (F3).
2. Declare the `acl` package dependency, or drop the `setfacl` shell-out entirely once
   step 1 removes the need for it.
3. Make `rollback.sh` re-run `systemd-tmpfiles` and fail loudly instead of `|| true`.
4. Fix the helper name in `verify-host.sh:20,33` to the installed
   `backbond-real-agent-demo-secret-preflight`, and re-run the verifier on the host.
5. Relax the `verify-host.sh:12` mode assertion to tolerate the ACL mask (F6).

## Boundary state re-confirmed, unchanged from the pre-candidate baseline

- Live container `f4938b7e4adb` `backbond/passive-monitor-demo:dcea859e…` — healthy, 11 days.
- `/var/lib/backbond-real-agent-demo/d9-one-shot.armed` — absent.
- `/var/lib/backbond-real-agent-demo/d9-one-shot.consumed` — absent.
- `/etc/backbond` `drwx------ root root`; `real-agent-demo.env` unreadable.
- `backbond-judge-client` group — absent (not installed, correct).
- `backbond-claude-judge.service` — active, socket LISTEN, untouched.
