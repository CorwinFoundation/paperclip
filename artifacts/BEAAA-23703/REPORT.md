# BEAAA-23703 — immutable d9 one-shot host-gate candidate

BLUF: this bundle supersedes commit `521c4138…` and the mixed local commit
`711d3e76`. It is rebuilt from an explicit file list around a single root
action: the existing fixed helper
`/usr/local/sbin/backbond-real-agent-demo-secret-preflight` is replaced in place
by an enhanced version that keeps the canonical d9 preflight byte for byte and
adds atomic consumption of the one-shot exact-d9 authorization. No second sudo
entry point, no new sudoers file, no repository installer executing other
repository content. Nothing was installed, armed or deployed. 129 unit tests and
35 end-to-end checks pass.

Contract: `contracts/are-scanner-api-v1.md@v1.3.0`, unchanged.
Classification: rule-execution for Scanner scoring, and a privileged
authorization-boundary change requiring CTO co-sign and exact-byte QA.

## Supersession and corrections to the record

| Candidate | Status |
| --- | --- |
| `521c413802b3b86f0b3b4e8fadcfdb616b1b4b4c` | SUPERSEDED by this bundle. Its pin was right; its design added a second root action. |
| `711d3e76` and tarball `39ff8394` | NOT APPROVED. Mixed design, and its installer refused to replace the live helper. |

Three corrections, each measured rather than asserted:

1. **The release pin is 906 files / `5d6398a3cbf567778a8ca9ae7254b82a286a782dbd2874f0f711e9292bf16c17`, and it does recompute.** Any earlier claim that it does not is withdrawn. `verify_release_manifest.py` derives it two independent ways and both print the same pair: `--from-git` materialises a real d9 checkout of the eleven workflow roots, and `--from-tree` walks a release extracted from the archive the workflow builds. The superseded 902 / `b6d3402d…` figure came from an eight-root list that omitted `release/backbond-scanner-release.json`, `release/backbond-scanner-release.schema.json`, `release_identity.py` and `artifacts/beaaa-18720/unified-probe-inventory.jsonl`. A 902 gate rejects the genuine payload at the extract step.
2. **One root action, not two.** The superseded candidate installed a second helper, `backbond-real-agent-demo-authorize`, with its own sudoers rule. Because d9's `deploy.sh` already invokes the secret-preflight helper as root, that made two root entry points. This bundle ships no `backbond-real-agent-demo-authorize` and no `.sudoers` file. The existing single rule is verified unchanged.
3. **The live helper being byte-identical to canonical d9 is the expected baseline, not a reason to leave it alone.** It is backed up and atomically replaced at the same fixed path, and rollback restores the backed-up `e1d50aec…` bytes.

Also removed from the candidate directory: `install_host_gate.py`'s refusal to
replace an existing target, a modified copy of the secret-preflight helper
(`5e228cb1…`, which is *not* the canonical `e1d50aec…`), an unpinned
`sudoers-backbond-real-agent-demo`, and all generated caches.

## Candidate files

Paths are relative to this bundle. `SHA256SUMS` records the same values and
`sha256sum -c SHA256SUMS` passes for every shipped file.

| File | SHA-256 | Installs to | Owner | Mode |
| --- | --- | --- | --- | --- |
| `candidate/backbond-real-agent-demo-ssh-gate` | `bd564caf524ccec27cdf00d1b85e7ef18cadbcdc2e4163a605565c19578cab93` | `/usr/local/sbin/backbond-real-agent-demo-ssh-gate` | `root:root` | `0755` |
| `candidate/backbond-real-agent-demo-secret-preflight` | `927fca8b796e7e868a319ab2fffb4372e34bb29228723090f31f6b994a46f6f8` | `/usr/local/sbin/backbond-real-agent-demo-secret-preflight` | `root:root` | `0755` |
| `candidate/MANIFEST.json` | `78fea504a48f01104aea14099f8b60b6c4dfcb64422e9733c70c9448cf667fb2` | placement contract | | |
| `candidate/INSTALL.md` | `453022053f2e5f141ee38355067f26ec221598f0ee4546a60e7bd6397240fc3a` | operator procedure | | |
| `candidate/install_host_gate.py` | `005d6186d31eb59d5631776e41b0c3a622a3321ed33807bfe6648f3b37039e14` | run by the host owner | | |
| `candidate/verify_release_manifest.py` | `6059710cb3fa7c8bb40e861d255368bf1d3de45a43cf27886d743760e4b6a2ab` | pin verifier | | |
| `candidate/tests/test_d9_host_gate.py` | `c135ecd1e5949722224074e19f85eb92cd01825d6f15dfe64b58f8634ce52baa` | | | |
| `candidate/tests/e2e_forced_command.sh` | `0f2e77f4fa301900f28816b758e71baf0c03e0f8fb128e8a70bcb21095e679d1` | | | |

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

`INSTALL.md` is the procedure and `install_host_gate.py` implements it:
verify sources against the manifest, verify each current target against its
pinned `replaces_sha256`, back it up into a root-owned `0700` directory, stage
in the same directory, set uid/gid/mode, validate with a fixed system validator,
atomically rename, then re-attest. `py_compile` and `visudo -cf` are the only
programs it runs and neither executes the file it checks; the installer contains
no reference to `deploy.sh`, `os.system`, `shell=True` or `os.execv`, and a test
asserts that. It refuses to run while an arm file exists and never creates one.

`--verify-only` is the default and mutates nothing. `--rollback --confirm`
restores every backed-up target by the same atomic rename; restoring the helper
to `e1d50aec…` alone removes the one-shot behaviour and returns the host to
canonical d9 preflight.

Note for the CTO: BEAAA-23673 F3 prohibits executing repository-controlled
installers as root, and the board has twice asked for a fixed installer. Both are
served here by shipping the installer as pinned, reviewable bytes that implement
`INSTALL.md`, which remains sufficient on its own. If the CTO prefers the
prohibition read strictly, drop `install_host_gate.py` from the approved set and
follow `INSTALL.md` by hand; nothing else in the bundle depends on it.

## Tests

```
cd candidate
D9_REPO=<clone holding d9> python3 -m pytest -q tests/test_d9_host_gate.py   # 129 passed
bash tests/e2e_forced_command.sh /path/to/real-agent-demo-release.tgz        # 35 passed, 0 failed
```

The unit suite loads the shipped files themselves and covers the allowlist and
the full denial table, the release pin, the deploy leg's exec argv and
environment, the preflight's preserved behaviour and failure strings, the
authorization's ownership, mode, content, fstat revalidation, single consumption
and replay denial, the installer's verified backup, atomic replacement, rollback
to `e1d50aec…`, refusal while armed and refusal on unexpected current bytes, and
that the recorded hashes recompute.

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
- The helper now fails closed when unarmed, so the previously attested
  "preflight passes" check becomes "preflight prints, then exits 1 with
  `deploy_authorization: absent`". `INSTALL.md` and `MANIFEST.json` state the new
  expected result.
- `/etc/sudoers.d/backbond-real-agent-demo-secret-preflight` is pinned at
  `2a56c29c…`, the canonical d9 bytes. That file is `0440 root:root` and was not
  readable from this seat, so the value is the canonical blob and the installer
  verifies it as root. A mismatch is a stop.
- The gate assumes `/bin/tar`, `/bin/bash` and `/usr/bin/python3` at fixed paths,
  with no PATH resolution in either program.

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
