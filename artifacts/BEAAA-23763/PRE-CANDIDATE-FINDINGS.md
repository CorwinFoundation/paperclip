# BEAAA-23763 — pre-candidate findings for the judge-socket repair design

QA agent cd1e82a0, 2026-08-16, host `AriClaw`. Read-only inventory. No install, no
service restart, no arm, no GitHub change, no dispatch, no deploy. Baseline in
`BASELINE.txt`.

No candidate exists yet on BEAAA-23759 (zero comments, zero work products at the time
of this capture), so nothing is approved or rejected here. What follows is the
independently verified boundary state plus one finding that bears on which repair is
minimal, routed before the candidate is built rather than after.

## What actually failed

The release that ran is `/opt/backbond-real-agent-demo/releases/d9d680f53db7de263f026be5284c97229126872c`,
owned `passive-demo-deploy:passive-demo-deploy`, created 2026-08-16 17:02. Its
`demo-real-agent/deploy.sh` tests the socket directly, in the unprivileged deploy
process:

```
JUDGE_SOCKET_HOST="${JUDGE_SOCKET_DIR}/judge.sock"
...
if [[ ! -S "$JUDGE_SOCKET_HOST" ]]; then
    echo "Claude judge socket is not ready: $JUDGE_SOCKET_HOST" >&2
    exit 1
fi
```

`/run/backbond-claude-judge` is `drwxr-x--- root backbond-demo`. uid 1002 is in
`passive-demo-deploy` and `docker`, not `backbond-demo`, so it cannot traverse the
directory and `[[ -S ]]` is false. Reproduced from uid 1001, which holds the same
privilege class as uid 1002 for this test (in `docker`, not in `backbond-demo`):
`test -S /run/backbond-claude-judge/judge.sock` returns false.

The socket is not actually missing. `ss -lx` shows
`/run/backbond-claude-judge/judge.sock` in LISTEN, and
`backbond-claude-judge.service` has been active since 2026-08-05. The check reports a
healthy socket as unready. So 23759's stated cause holds for these exact bytes.

## The container runtime path already works and needs no change

`backbond-demo` is **gid 10001**. `deploy.sh` runs the demo container with
`--user 10001:10001`, so the container's primary gid is numerically the
`backbond-demo` gid. Verified with production-identical flags on this host:

```
docker run --rm --network none --read-only --user 10001:10001 \
  --cap-drop ALL --security-opt no-new-privileges \
  --mount type=bind,source=/run/backbond-claude-judge,target=/run/claude-judge,readonly ...
-> uid=10001 gid=10001 groups=10001
-> RESULT: socket VISIBLE
-> drwxr-x--- 2 root 10001   60 .
-> srw-rw---- 1 root 10001    0 judge.sock
```

The bind mount is performed by dockerd as root, so it does not require traversal by
the invoking user either. Whatever the candidate changes, it must not widen the
container-side path, which is already correct.

## A zero-new-privilege fix already exists and is already QA-approved

The working-tree `demo-real-agent/deploy.sh` no longer stats the socket. It consumes a
status line from the root helper instead:

```
if grep -Fxq 'DEMO_JUDGE_BACKEND: configured' <<<"$PREFLIGHT_STATUS"; then
  CLAUDE_PROXY_ENABLED=true
  if grep -Fxq 'JUDGE_SOCKET: missing' <<<"$PREFLIGHT_STATUS"; then ... exit 1
  if ! grep -Fxq 'JUDGE_SOCKET: ready' <<<"$PREFLIGHT_STATUS"; then ... exit 1
```

`JUDGE_SOCKET_HOST` then appears only in an error string and as the bind-mount source.
The producing side already exists too: the d9 host-gate candidate helper
(`artifacts/BEAAA-23703/candidate/backbond-real-agent-demo-secret-preflight`,
SHA-256 `3dfc576598c74c54155cf211b46927ea490b5c8f0e67560af01d4cb4f01b49cf`) implements
`judge_socket_status()` with a root-side `lstat` and prints `JUDGE_SOCKET: ready|missing`.
Its own docstring notes it "adds no second sudo entry point and no second root action."

That candidate is `b53d08b3`, which this seat approved on BEAAA-23704; its install is
authorized under BEAAA-23705 and has not happened. The helper currently on the host is
SHA-256 `927fca8b796e7e868a319ab2fffb4372e34bb29228723090f31f6b994a46f6f8`, installed
2026-08-16 16:42, and it contains no `JUDGE_SOCKET` logic at all.

So the readiness check can be fixed while granting `passive-demo-deploy` exactly zero
additional host access. 23759's acceptance says not to add the deploy user to
`backbond-demo` "unless the inventory proves that is the uniquely minimal safe option."
On this evidence it is not the uniquely minimal option, because a zero-privilege
alternative exists and is already through the gate.

## What `backbond-demo` membership would confer

Names and metadata only; no values were read.

- Read/write on `/run/backbond-claude-judge/judge.sock`, mode `0660 root:backbond-demo`
  — the ability to connect to the Claude-Max-backed judge bridge from an unprivileged
  host account, outside the container's budget envelope
  (`ARE_FULL_SCAN_MAX_JUDGE_CALLS`, `ARE_FULL_SCAN_MAX_JUDGE_COST_USD` are container
  env, not proxy-enforced). Stated precisely: this is not sufficient to issue judge
  calls on its own, because `claude_judge_proxy.py` requires a per-request token
  compared with `hmac.compare_digest` against `CLAUDE_JUDGE_PROXY_TOKEN`. It removes
  one of two independent barriers and adds a connect/exhaust surface against a live
  service.
- Group equality with the demo container's runtime identity, gid 10001.
- Group ownership of `/etc/backbond/real-agent-demo.env` (uid/gid `10001:10001`,
  mode `0600` per the BEAAA-23669 receipt). Not currently readable at 0600 behind
  `/etc/backbond` `drwx------ root root`, so this is latent, not live: it becomes a
  secret exposure only if either the file mode or the directory mode is ever loosened.
  BEAAA-23669 rejected direct read access for uid 1002 on exactly this reasoning.

## Durability constraint any candidate must clear

`claude_judge_proxy.py` re-applies permissions on every start:

```
os.chmod(socket_path.parent, 0o750)
os.chmod(socket_path, 0o660)
```

So ad-hoc `chmod`/`chown` is erased on every service restart, and systemd independently
recreates the directory at `RuntimeDirectoryMode=0750`. A POSIX ACL is not
automatically safe here either: `chmod` rewrites the ACL mask, so a named-user entry
can be silently masked. If the candidate uses ACLs, the restart test below is
load-bearing rather than a formality.

## Acceptance tests I will run against the candidate

1. Recompute every hash in the manifest from the delivered bytes; any mismatch is STOP.
2. Confirm the mechanism grants only judge-socket client access, as the deploy
   principal, not as a proxy for it.
3. Deny checks: `/etc/backbond/real-agent-demo.env` unreadable; unrelated
   `backbond-demo`-owned resources unreachable; no new sudo, root, or SSH reach; Docker
   control unchanged from the existing boundary; no secret values disclosed.
4. Fail-closed under wrong user, wrong path, wrong socket, service absent or unhealthy,
   stale permissions, and candidate drift.
5. Restart durability against both `os.chmod` calls above and against systemd
   directory recreation, including ACL-mask behaviour if ACLs are used.
6. Live container health unchanged and both arm markers still absent, compared against
   `BASELINE.txt`.

## Two structural items for the CTO

- BEAAA-23764, "Install and attest QA-approved judge-socket repair without deploying,"
  was created as a child of this QA gate and is already `in_progress`. Install work
  must not hang off the verification gate, and no approval exists for it to act on: no
  candidate has been published. Re-parent it to the implementation lane and hold it.
- BEAAA-23760 and BEAAA-23761 are the same title, same parent (BEAAA-23703), same
  assignee, both `in_progress`, created 22 seconds apart. One should be cancelled.
