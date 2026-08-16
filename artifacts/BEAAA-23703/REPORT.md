# BEAAA-23703 — immutable d9 one-shot host-gate candidate

BLUF: this bundle supersedes commit `48fd6b3e…` and repairs the six findings the
CTO and QA raised on BEAAA-23726 and BEAAA-23704. The candidate now ships **no
installer**: no repository-provided program is authorized to run as root, and
`INSTALL.md` carries the complete manual root-owner procedure. Rollback is
bounded to the two replacement targets and never recreates the stale a68 sudo
authority. The tarball is rebuilt from a clean staging directory and its members
match the commit, the manifest and `SHA256SUMS` exactly. Nothing was installed,
armed or deployed. 144 unit tests and 35 end-to-end checks pass.

Contract: `contracts/are-scanner-api-v1.md@v1.3.0`, unchanged.
Classification: rule-execution for Scanner scoring, and a privileged
authorization-boundary change requiring CTO co-sign and exact-byte QA.

## Supersession

| Candidate | Status |
| --- | --- |
| `521c413802b3b86f0b3b4e8fadcfdb616b1b4b4c` | SUPERSEDED. Its pin was right; its design added a second root action. |
| `711d3e76` and tarball `39ff8394` | NOT APPROVED. Mixed design. |
| `48fd6b3e2f0cddde520f069db3f73b48af0cbaf2`, tarball `8532eab0…` | SUPERSEDED by this bundle. QA STOP on BEAAA-23704, comment `830358fa`. |

Everything QA reproduced and passed on `48fd6b3e` is unchanged here: the release
pin of 906 files / `5d6398a3cbf567778a8ca9ae7254b82a286a782dbd2874f0f711e9292bf16c17`,
both verifier routes, the gate and helper bytes, the allowlist and its denial
table, and the one-shot authorization design. Those two helper files and
`verify_release_manifest.py` are byte-identical to `48fd6b3e`.

## What changed since `48fd6b3e`

**F4 — the installer is gone.** The CTO ruled on BEAAA-23726 that a
repository-controlled program designed to run as root violates the BEAAA-23673
F3 boundary regardless of what its subprocesses do. The installer that
`48fd6b3e` shipped is deleted from the commit, the tarball, the manifest, the
checksum inventory and this report; no file in the bundle carries its name, and
nothing in the bundle tells anyone to run it. A test asserts that absence across
`INSTALL.md`, `MANIFEST.json`, this report and `SHA256SUMS`. The host owner
performs the fixed administrative operations in `INSTALL.md`; the only programs
that run as root are system programs, and the reviewed candidate bytes are
copied and hash-verified, never executed.

**F1 — rollback no longer restores the removal targets.** The documented
rollback restores exactly two paths, the helper and the gate, from the backup
set. It then *asserts* that `/etc/sudoers.d/backbond-real-agent-demo` and
`/usr/local/sbin/backbond-real-agent-demo-deploy` are still absent and stops if
either has reappeared, re-runs `/usr/sbin/visudo -c`, and re-attests the final
state with `sha256sum` and `stat`. The procedure contains no step that recreates
the stale a68 authority; returning to that posture would be a new privileged
change through BEAAA-23702. The manifest states the same contract in
`rollback.restores`, `rollback.never_restores` and `remove[].restored_by_rollback`.

**F2 — the tarball is the pinned set.** It is built from a clean staging
directory populated by an explicit nine-file list, with no `.pytest_cache`, no
`__pycache__` and no `.pyc`. Tar members, commit contents, `MANIFEST.json` and
`SHA256SUMS` agree exactly, and the build is reproducible.

**F3 — a failed installation stops and leaves a coherent host.** The procedure
runs under `set -eu` and every check calls `stop()`, which prints the failure and
directs the operator to the rollback. `INSTALL.md` has an explicit *If any step
fails* branch: STOP, leave unarmed, do not deploy, run the bounded rollback,
validate with `/usr/sbin/visudo -c`, attest that the stale a68 authority is still
absent, and report to BEAAA-23702. The order is also fixed so a partial run can
only narrow the host: both a68 removals happen **before** either replacement, so
an abort part-way through leaves fewer privileges than it started with, never the
mixed weaker state QA described.

**F5 — validation leaves no cache residue.** `py_compile` is not used anywhere;
it would write a `__pycache__` directory beside the staged file inside
`/usr/local/sbin`. The validator is
`/usr/bin/python3 -c 'import ast,sys; ast.parse(open(sys.argv[1],"rb").read())'`,
which parses the staged file without importing, executing or byte-compiling it
and writes nothing. That string is the `validation.command` field, both
`install[].validator` fields, and the `INSTALL.md` step, character for character;
a test compares them. The procedure asserts no `__pycache__` and no `.pyc` beside
each helper immediately after replacement and again for the directory at the end,
and `post_install_verification` repeats the check.

**F6 — backups are unique and never overwritten.** Each attempt creates
`/root/beaaa-23703-backup-<UTC timestamp>-<pid>` with `mkdir -m 0700`, without
`-p`, which is the fail-if-exists operation: an attempt that reaches an existing
directory stops instead of overwriting a rollback set. Files are stored under
their full target path inside that directory, never by basename, so no copy can
collide with another, and `BACKUP.sha256` records the four hashes. The directory
is echoed as `backup_directory=…` because the rollback needs it. A rehearsal
proves a second attempt with the same identifier stops with the first attempt's
backup set intact.

## Candidate files

Paths are relative to this bundle. `SHA256SUMS` records the same values and
`sha256sum -c SHA256SUMS` passes for every shipped file.

| File | SHA-256 | Installs to | Owner | Mode |
| --- | --- | --- | --- | --- |
| `candidate/backbond-real-agent-demo-secret-preflight` | `927fca8b796e7e868a319ab2fffb4372e34bb29228723090f31f6b994a46f6f8` | `/usr/local/sbin/backbond-real-agent-demo-secret-preflight` | `root:root` | `0755` |
| `candidate/backbond-real-agent-demo-ssh-gate` | `bd564caf524ccec27cdf00d1b85e7ef18cadbcdc2e4163a605565c19578cab93` | `/usr/local/sbin/backbond-real-agent-demo-ssh-gate` | `root:root` | `0755` |
| `candidate/MANIFEST.json` | `23ca04140bac53c038b62c89f3fd63e612e658a884edb00f9a1986876c1a14b2` | placement contract | | |
| `candidate/INSTALL.md` | `4ac69d544938008b3fac9643c8166a4436649d2e01b1a3089a1a0e652ed818a5` | manual procedure | | |
| `candidate/verify_release_manifest.py` | `6059710cb3fa7c8bb40e861d255368bf1d3de45a43cf27886d743760e4b6a2ab` | pin verifier | | |
| `candidate/tests/test_d9_host_gate.py` | `ad6c899ec876fe909317440acd2a9f02d61fef9167f917860153478ad31441b3` | | | |
| `candidate/tests/e2e_forced_command.sh` | `0f2e77f4fa301900f28816b758e71baf0c03e0f8fb128e8a70bcb21095e679d1` | | | |

Nine files ship in the tarball: the seven above, `REPORT.md` and `SHA256SUMS`.
Nothing else.

## The single root action

d9's `deploy.sh` already runs, through the one existing sudoers rule:

```
sudo --non-interactive /usr/local/sbin/backbond-real-agent-demo-secret-preflight
```

That is the only elevation in a deployment, and this candidate keeps it that
way. The helper at that path is replaced by one that:

1. performs the canonical d9 names-only secret preflight, unchanged, and prints
   `secret_file: valid` plus the `<NAME>: configured` lines that `deploy.sh`
   greps;
2. then requires `/etc/backbond/real-agent-demo-deploy.allow` to be a regular
   root-owned `0600` file naming exactly d9, validating ownership and mode on
   the opened descriptor and not only on the pre-open `lstat`;
3. then consumes it by renaming it to `real-agent-demo-deploy.consumed` in the
   same directory. The rename is the atomic consumption, so a replay finds
   nothing and fails closed.

An authorization naming any other release is refused and left in place. With
nothing armed the helper exits non-zero, `deploy.sh` aborts under `set -e`
before docker is reached, and deployment is impossible.

The preflight is reported *before* the authorization is consumed so an unarmed
post-install check still exercises and prints the secret boundary, then stops
with `deploy_authorization: absent` and exit status 1. That exit 1 is the
correct fail-closed result, not an installation failure.

Evidence that the preflight itself is untouched: diffing the shipped helper
against canonical d9 `e1d50aec…` removes exactly one line, the old one-line
module docstring, and adds 95. Every canonical code path is byte-identical, and
a test asserts precisely that.

The forced-command gate contains no `sudo` call at all. A test strips its
docstring and asserts the code contains no `sudo`, no `pkexec` and no `setuid`,
and the end-to-end run puts a fake `sudo` early on `PATH` and asserts it is
never called.

## Exact allowlist

`SSH_ORIGINAL_COMMAND` is compared literally. Only these shapes are accepted,
where `<D9>` is `d9d680f53db7de263f026be5284c97229126872c` and `<REL>` is
`/opt/backbond-real-agent-demo/releases/<D9>`:

1. `mkdir -p <REL>`
2. the sftp subsystem string, with or without the trailing space sshd appends,
   or `scp -t <REL>/` and its three equivalent spellings; each routed to a
   write-only sink whose entire namespace is `<REL>/real-agent-demo-release.tgz`
3. `cd <REL> && tar -xzf real-agent-demo-release.tgz`
4. `DEMO_GIT_SHA='<D9>' DEMO_DEPLOYED_AT='<UTC instant>' bash '<REL>/demo-real-agent/deploy.sh' '<REL>'`,
   with the timestamp matching `YYYY-MM-DDTHH:MM:SSZ` and the SHA, script path
   and directory argument each compared against the pinned values

Everything else exits 1. The denial line carries the sha256 of the rejected
string rather than the string itself. The gate never passes client text to a
shell: no `shell=True`, no `os.system`, no interpolation.

Before executing `deploy.sh` the gate hashes the extracted release and requires
906 files and tree digest `5d6398a3…`, so a tampered or wrong-SHA payload never
reaches execution. `deploy.sh` then runs under the unprivileged deployment
account; the gate refuses to start if its own euid or egid is 0.

## Stale a68 authority removed

`MANIFEST.json` records both removals with the digests I read from this host:
`/etc/sudoers.d/backbond-real-agent-demo` (`37b24501…`) and
`/usr/local/sbin/backbond-real-agent-demo-deploy` (`956065275…`). After
installation no root deploy wrapper exists and the deployment account holds
exactly one sudo authority. The SSH key and its options, the protected env file,
docker access, filesystem ownership and repository permissions are untouched. No
secret value was read or recorded.

## Installation and rollback

`INSTALL.md` is the whole procedure. It is written as one shell block per phase
so it can be read, audited and rehearsed as the exact text an operator runs:
verify the reviewed sources against the manifest, verify every current target
against its pinned digest including the sudoers rule that must not change, create
a unique fail-if-exists `0700` backup directory and copy all four affected paths
into it path-preserving, remove the stale a68 sudoers drop-in and re-parse
sudoers, remove the stale root deploy wrapper, then replace each helper by
staging in the same directory, setting uid/gid/mode, validating without
executing, comparing bytes, renaming atomically and re-attesting. `HOSTROOT` is
empty for a real installation and exists only so the same text can be rehearsed
against a fake host root.

The unit suite extracts both blocks from `INSTALL.md` by their markers and runs
them. Against a fake host root seeded with the exact bytes this candidate
expects to replace, the rehearsal proves:

- installation completes, both helpers hold the reviewed digests, and both a68
  artifacts are gone;
- **rollback leaves both a68 artifacts absent** — the F1 defect, tested the way
  QA proved it — while the helper returns to canonical `e1d50aec…`;
- the backup set holds all four paths under their full target paths, `0700`, with
  a four-line `BACKUP.sha256`;
- a second attempt with the same identifier stops and the first backup set is
  bit-for-bit intact;
- an abort during the second replacement leaves a strictly narrower host, and the
  bounded rollback recovers it without restoring a68 authority;
- no `__pycache__` and no `.pyc` appears beside either helper;
- nothing is armed, and this host's own `/usr/local/sbin` helpers still hold
  `e1d50aec…` and `44ff5dfa…` afterwards.

Stated plainly: the rehearsal seeds the two sudoers files with stand-in bytes,
because they are `0440 root:root` on the real host and unreadable from this seat,
and substitutes those two pinned digests in the procedure text. Every other
constant, including both reviewed source digests, runs exactly as shipped, and
`render()` asserts each substitution is the single line it claims to be. The
`chown` and `visudo` calls are stubbed because the rehearsal is rootless.

## Tests

```
cd candidate
D9_REPO=<clone holding d9> python3 -m pytest -q tests/test_d9_host_gate.py   # 144 passed
bash tests/e2e_forced_command.sh /path/to/real-agent-demo-release.tgz        # 35 passed, 0 failed
```

The unit suite loads the shipped files themselves and covers the allowlist and
the full denial table, the release pin, the deploy leg's exec argv and
environment, the preflight's preserved behaviour and failure strings, the
authorization's ownership, mode, content, fstat revalidation, single consumption
and replay denial, the absence of any installer, the manual procedure's bounded
rollback, unique backups, non-executing validation and failure branch, and that
the recorded hashes recompute with no cache in the shipped set.

The end-to-end script runs a real unprivileged `sshd` driven by real `ssh`, `scp`
and `sftp`. It proves the permitted shapes work over both the SFTP and legacy
scp transports, that the extract prints `files=906 tree_sha256=5d6398a3…`, that
the deploy leg reaches execution with the exact argv and a six-name environment,
that wrong-SHA and alternate commands are denied, that the sink refuses writes
outside the pinned path and refuses reads, that a tampered release never reaches
execution, and that the gate never invokes sudo.

Isolation: the machine that produced this candidate is the demo host itself. The
end-to-end script relocates the release root into a temporary directory through
a shim and replaces `os.execve` with a recorder, so it cannot write under
`/opt/backbond-real-agent-demo` or run `deploy.sh`. It asserts at the end that no
d9 release directory exists on the host, that nothing is armed, and that the
running containers are unchanged. All three passed; live container
`f4938b7e4adb` was healthy and untouched throughout.

## Bundle build

The tarball is built from a clean staging directory populated by the explicit
nine-file list, with fixed metadata so the digest reproduces:

```
tar --sort=name --format=gnu --numeric-owner --owner=0 --group=0 --mtime=@0 \
    -cf - -C <staging> REPORT.md SHA256SUMS \
    candidate/MANIFEST.json candidate/INSTALL.md \
    candidate/backbond-real-agent-demo-secret-preflight \
    candidate/backbond-real-agent-demo-ssh-gate \
    candidate/verify_release_manifest.py \
    candidate/tests/test_d9_host_gate.py candidate/tests/e2e_forced_command.sh \
  | gzip -n -9 > BEAAA-23703-d9-host-gate-candidate.tar.gz
```

`tar tzvf` lists exactly those nine members and nothing else: no directory
entries, no `.pytest_cache`, no `__pycache__`, no `.pyc`. Every member's sha256
equals the value in `SHA256SUMS` and in the commit, every member is `0/0` with
`mtime` 0, and two builds from the same bytes produce the same archive digest.

## Workflow compatibility

The d9 deploy job runs plain `scp`, and OpenSSH 9.0 and later use the SFTP
protocol for it; the runner ships 9.6p1. Under a forced command sshd runs the
gate instead of `sftp-server`, so a gate accepting only `scp -t …` would deny the
upload. This candidate accepts both transports and confines each to the one
pinned archive path. Verified locally on OpenSSH 9.6p1, the runner's version.

Two further defects were found and fixed while producing this candidate, each of
which would have failed the single authorized attempt: `tar --no-absolute-names`
does not exist in GNU tar 1.35 and made every extract exit 64; and the sftp
subsystem string arrives with a trailing space, which an exact-set match denied.

## Scope limits, stated plainly

- The tree pin covers paths, content and entry type, not permission bits,
  because tar applies the account umask. Nothing in the release is executed by
  its permission bits.
- Between verification and `exec` the deployment account could alter its own
  release directory. That is the same principal on both sides, which is why no
  root process reads that tree.
- The helper fails closed when unarmed, so the previously attested "preflight
  passes" check is "preflight prints, then exits 1 with
  `deploy_authorization: absent`". `INSTALL.md` and `MANIFEST.json` state the new
  expected result.
- `/etc/sudoers.d/backbond-real-agent-demo-secret-preflight` is pinned at
  `2a56c29c…`, the canonical d9 bytes. That file is `0440 root:root` and was not
  readable from this seat, so the value is the canonical blob and the operator
  verifies it as root. A mismatch is a stop.
- The gate assumes `/bin/tar`, `/bin/bash` and `/usr/bin/python3` at fixed paths,
  with no PATH resolution in either program.
- The rehearsal proves the documented text behaves as claimed on a fake root as
  an unprivileged user. It is not a root installation, and it does not replace
  the operator's own attestation on BEAAA-23705.

## Operational metadata

- scanner_version: v0.5.0, unchanged
- corpus_version: unchanged
- probe-set-version: unchanged
- rubric_point_id: none; no rubric, scoring, weight or probe surface is touched
- N=5 calibration delta: 0 on the golden set by construction. This is host
  deployment plumbing outside the harness and scoring path; no harness file is
  modified, so the ±0 harness invariant does not apply.
- cto_cosign_ref: pending on BEAAA-23702
- Not installed, not armed, not deployed. No workflow dispatch, no repository
  variable change, no image build, no container change.
