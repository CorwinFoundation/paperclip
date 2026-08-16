# BEAAA-23703 — immutable d9 one-shot host-gate candidate

BLUF: this bundle is the exact candidate bytes for the BEAAA-23702 repair. It binds the
Real Agent demo forced command to commit `d9d680f53db7de263f026be5284c97229126872c`,
denies every other SHA, argument and command, keeps uploaded and repository code under the
unprivileged deployment account, and reduces root involvement to one fixed no-argument
helper that atomically consumes a one-shot d9 authorization. Nothing here was installed,
armed or deployed. 116 unit tests and 37 end-to-end checks against a real OpenSSH server
pass. Producing the candidate found two defects that would each have burned the single
authorized deployment attempt; both are fixed and covered by tests.

Contract: `contracts/are-scanner-api-v1.md@v1.3.0`, unchanged by this work.
Classification: rule-execution for Scanner scoring, and a privileged authorization-boundary
change that requires CTO co-sign and exact-byte QA approval before any installation.

## Candidate files

| File | SHA-256 | Installs to | Owner | Mode |
| --- | --- | --- | --- | --- |
| `backbond-real-agent-demo-ssh-gate` | `6ab6920cdf878419b8cc001f89e4e2175651688ce1f9698390144034315966c1` | `/usr/local/sbin/backbond-real-agent-demo-ssh-gate` | `root:root` | `0755` |
| `backbond-real-agent-demo-authorize` | `3bf20ac02ef94c23cee7aaa673dbdd35e3d9bec51e91dfa0f951ab4f48f46cc0` | `/usr/local/sbin/backbond-real-agent-demo-authorize` | `root:root` | `0755` |
| `backbond-real-agent-demo-authorize.sudoers` | `12ece5c9137747e0166463de09f905895ee076f5280a9618b87edc51328b015b` | `/etc/sudoers.d/backbond-real-agent-demo-authorize` | `root:root` | `0440` |
| `install-manifest.json` | `e5f233f09812a60fe63002c63fca09919a75eeb569cd1342bfaf7ca10e7c25be` | not installed (placement contract) | — | — |
| `verify_release_manifest.py` | `43c0b292c5b69a27753adf19ee8515491b7f5d51d8f7d3d99d7f11cac9df971a` | not installed (pin verifier) | — | — |
| `tests/test_d9_host_gate.py` | `6436e19d975a965413fda0503176ee913ebcc901e5f7fcf99c03c2ad20f50a7a` | not installed | — | — |
| `tests/e2e_forced_command.sh` | `4f650921a6d2b844054516789ba8dcfaff043c8223c367a24fd98eb8ea79b16f` | not installed | — | — |

`SHA256SUMS` records the same values. `sha256sum -c SHA256SUMS` recomputes them exactly, and
`tests/test_d9_host_gate.py::test_recorded_hashes_recompute` asserts it.

The gate installs over the stale `44ff5dfa0faac3174bc97b0546a51194a16ce2fac40ac15f9da984460d5d323f`
gate at the same path, so the deployment key's `authorized_keys` entry does not change. This
candidate authorizes no edit to that file, its options, or the key material.

## Exact allowlist

`SSH_ORIGINAL_COMMAND` is compared literally. Only these four shapes are accepted, where
`<D9>` is `d9d680f53db7de263f026be5284c97229126872c` and
`<REL>` is `/opt/backbond-real-agent-demo/releases/<D9>`:

1. `mkdir -p <REL>` — creates the release directory, mode `0755`, and nothing else.
2. The sftp subsystem string, one of `internal-sftp`, `sftp-server`,
   `/usr/lib/openssh/sftp-server`, `/usr/lib/ssh/sftp-server`,
   `/usr/libexec/openssh/sftp-server`, `/usr/libexec/sftp-server`, with or without the
   trailing space sshd appends. Routed to a write-only SFTP v3 sink whose entire namespace
   is the single path `<REL>/real-agent-demo-release.tgz`. Reads, directory listings,
   renames, removals and every other path are refused with `SSH_FX_PERMISSION_DENIED`.
3. `scp -t <REL>/` and its three equivalent spellings — a legacy scp sink that accepts one
   plain file whose control-record name must be `real-agent-demo-release.tgz`.
4. `DEMO_GIT_SHA='<D9>' DEMO_DEPLOYED_AT='<UTC instant>' bash '<REL>/demo-real-agent/deploy.sh' '<REL>'`
   where the timestamp must match `YYYY-MM-DDTHH:MM:SSZ`. The SHA, the script path and the
   directory argument are each compared against the pinned values after the match.

Anything else is denied with exit status 1. The denial line carries the sha256 of the
rejected string rather than the string itself, so client-controlled text never lands in the
host log.

The extract shape `cd <REL> && tar -xzf real-agent-demo-release.tgz` is accepted as the
fifth literal string and is executed as a fixed argv with `cwd=<REL>`. The gate never passes
client text to a shell: there is no `shell=True`, no `os.system`, and no interpolation of
`SSH_ORIGINAL_COMMAND` into any command line.

## The one-shot root action

`deploy.sh` and every other file in the release run as the deployment account. The gate
refuses to start at all if its effective uid or gid is 0. The single privileged step is:

```
/usr/bin/sudo --non-interactive /usr/local/sbin/backbond-real-agent-demo-authorize
```

with no arguments, granted by one sudoers line in the same form as the already-attested
secret-preflight rule:

```
#1002 ALL=(root) NOPASSWD: /usr/local/sbin/backbond-real-agent-demo-authorize ""
```

The helper reads `/etc/backbond/real-agent-demo-deploy.allow`, requires it to be a regular
root-owned `0600` file whose content is exactly the d9 SHA, and consumes it by renaming it to
`real-agent-demo-deploy.consumed` in the same directory. The rename is the atomic
consumption, so a replay finds no authorization and fails closed. A grant naming any other
SHA is refused and left in place. Ownership and mode are validated on the opened descriptor,
not only on the pre-open `lstat`, which is the BEAAA-23673 F1 finding applied here.

Order inside the deploy leg is deliberate: verify the extracted tree, then consume the
authorization, then exec. A release that fails verification never spends the grant.

## Release content pin

Before the authorization is consumed, the gate hashes the extracted release and requires
906 files and tree digest
`5d6398a3cbf567778a8ca9ae7254b82a286a782dbd2874f0f711e9292bf16c17`.
This replaces the exact-release check the removed a68 root wrapper performed, moved to the
unprivileged side so that no root process reads a user-writable tree.

`verify_release_manifest.py` recomputes the pin two independent ways, from the d9 blobs in
git and from an extracted release directory. Both agree:

```
$ verify_release_manifest.py git <repo> d9d680f53db7de263f026be5284c97229126872c
906 5d6398a3cbf567778a8ca9ae7254b82a286a782dbd2874f0f711e9292bf16c17
$ verify_release_manifest.py tree /path/to/extracted/release
906 5d6398a3cbf567778a8ca9ae7254b82a286a782dbd2874f0f711e9292bf16c17
```

The 906 files are the tracked contents of the exact path list the d9 deploy job hands to
`tar`, which at d9 includes `release`, `release_identity.py` and
`artifacts/beaaa-18720/unified-probe-inventory.jsonl`.

## Stale a68 authority removed

`install-manifest.json` records two removals with their expected digests: the sudoers drop-in
`/etc/sudoers.d/backbond-real-agent-demo` (`37b24501…`), which granted a68 preflight and
deploy, and `/usr/local/sbin/backbond-real-agent-demo-deploy` (`956065275…`), the root
release runner pinned to a68. After installation no root deploy wrapper exists and the
deployment account's sudo rights are exactly two zero-argument helpers: the attested secret
preflight, unchanged, and the new authorization helper.

Nothing else widens. The SSH key, its options, sudoers beyond the one new line, filesystem
ownership, docker access, secret files and repository permissions are untouched. No secret
value was read or recorded while producing this candidate.

## Two defects found while producing the candidate

Both would have failed the single authorized attempt closed.

1. An earlier draft of the gate passed `--no-absolute-names` to `tar`. GNU tar 1.35 does not
   accept that option, so every extract would have exited 64. The gate now runs
   `tar -xzf real-agent-demo-release.tgz --no-same-owner` and pre-scans the archive member
   list for absolute and parent-directory paths, which is the control that matters.
2. An earlier draft matched the sftp subsystem string exactly. sshd reports it with a
   trailing space when the `Subsystem` line has no arguments, so the workflow's own upload
   was denied. Observed directly:
   `SSH_ORIGINAL_COMMAND=[/usr/lib/openssh/sftp-server ]`. The gate now compares after
   stripping trailing spaces, and a subsystem string carrying real arguments is still denied.

An earlier draft also pinned 902 files and a digest computed from the wrong path list. The
pin now recomputes from the d9 tree in git, and a test asserts it.

## Workflow compatibility

The deploy job at d9 runs plain `scp`. OpenSSH 9.0 and later use the SFTP protocol for
`scp` by default, and the GitHub `ubuntu-latest` runner ships 9.6p1. Under a forced command
the subsystem request does not fail; sshd runs the forced command instead and reports the
configured subsystem in `SSH_ORIGINAL_COMMAND`. A gate that only accepted `scp -t …` would
therefore deny the upload. This candidate accepts both transports and confines each to the
one pinned archive path, so it works whether the runner uses the SFTP or the legacy protocol.

Verified locally against OpenSSH 9.6p1, the same version as the runner: a real `scp` upload
completes byte-identical over both transports, `scp -O` included.

## Tests

```
cd <candidate>
D9_REPO=<clone with d9> python3 -m pytest -q tests/test_d9_host_gate.py     # 116 passed
bash tests/e2e_forced_command.sh /path/to/real-agent-demo-release.tgz       # 37 passed, 0 failed
```

The unit suite loads the candidate files themselves and covers the allowlist, the denial
table including every wrong-SHA variant, the release pin, the ordering of verification
before consumption, the exec argv and environment, the helper's zero-argument and
ownership contracts, its fstat revalidation, single consumption and replay denial, the
sudoers bytes and `visudo -cf`, and the recorded hashes.

The end-to-end script runs a real unprivileged `sshd` and drives the gate with real `ssh`,
`scp` and `sftp`. It proves the four shapes work, the pin matches the real extracted d9
release, an unarmed deploy fails closed, an armed deploy consumes the grant and reaches
execution with the exact argv and a six-name environment, a replay is denied, a tampered
release is denied without spending the grant, and the sink refuses writes outside the pinned
path and refuses reads entirely.

Isolation: the end-to-end script relocates the release root into a temporary directory
through a shim and replaces `os.execve` with a recorder, so it cannot write under
`/opt/backbond-real-agent-demo` and cannot run `deploy.sh`. This matters because the machine
that produced this candidate is the demo host itself. The script asserts at the end that no
d9 release directory exists on the host and that the running containers are unchanged; both
assertions passed. The live container `f4938b7e4adb` on image
`backbond/passive-monitor-demo:dcea859ef502db9973fc98c62d6b6d144a8f0a65` was healthy before
and after and was never touched.

## Rollback

`install-manifest.json` carries the full sequence. In summary: restore the previous gate from
the root-owned pre-install backup or remove it, remove the authorization helper, remove the
new sudoers drop-in and re-run `visudo -c`. Restoring the a68 sudoers and root wrapper is
listed but should only follow an explicit decision to return to the stale posture. Removing
`/etc/backbond/real-agent-demo-deploy.allow` restores the fail-closed state in every case,
because with no authorization present the deploy leg cannot proceed.

## Preconditions for QA and the host owner

Each is a read-only check; a mismatch is a stop, not a fix, because this candidate does not
authorize the corresponding change.

- The `authorized_keys` forced command already points at
  `/usr/local/sbin/backbond-real-agent-demo-ssh-gate`.
- The sshd `Subsystem sftp` value is one of the six accepted shapes.
- `/etc/backbond` is root-owned and not group or world writable.
- The deployment account can reach docker without sudo, which `deploy.sh` at d9 requires
  since it is no longer run by a root wrapper.
- No arm file and no d9 release directory exist before installation.

## Scope limits, stated plainly

- The tree pin covers paths, content and entry type. It does not pin permission bits,
  because `tar` applies the account umask on extraction. Nothing in the release is executed
  by its permission bits.
- Between verification and `exec` the deployment account could still alter its own release
  directory. That is the same principal on both sides of the window, not a privilege
  boundary, and it is why no root process reads that tree.
- The sudoers line uses the `#1002` uid form, matching the attested secret-preflight rule. If
  the deployment account's uid is not 1002, stop and report.
- The gate assumes `/bin/tar`, `/bin/bash`, `/usr/bin/sudo` and `/usr/bin/python3` at fixed
  paths, with no PATH resolution anywhere in either program.

## Operational metadata

- scanner_version: v0.5.0, unchanged
- corpus_version: unchanged
- probe-set-version: unchanged
- rubric_point_id: none; no rubric, scoring, weight or probe surface is touched
- N=5 calibration delta: 0 on the golden set by construction. This candidate is host
  deployment plumbing outside the harness and scoring path, so no scoring behaviour can
  move. The ±0 harness invariant does not apply; no harness file is modified.
- cto_cosign_ref: pending on BEAAA-23702
- Not installed, not armed, not deployed. No workflow dispatch, no repository variable
  change, no image build, no container change.
