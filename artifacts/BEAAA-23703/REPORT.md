# BEAAA-23703 immutable d9 one-shot host-gate candidate

**BLUF.** The candidate bytes for BEAAA-23702 are complete and hashed. They bind the passive-demo
host gate to `d9d680f53db7de263f026be5284c97229126872c` and reject every other SHA, argument and
command. The uploaded release stays under the unprivileged deployment account, `deploy.sh` is never
run as root, and the one root action is a fixed no-argument helper that atomically consumes a
root-owned one-shot d9 authorization. Stale `a68e7e0969b45e7eb8add18ff2007a305a0156aa` authority is
removed and every permission is the same or narrower than before. Nothing was installed, armed or
deployed. 71 of 71 tests pass. Two items need a decision from BEAAA-23702 before installation, in
sections 8 and 9.

Contract: `contracts/are-scanner-api-v1.md@v1.3.0`. Classification: rule-execution, no scoring path
touched (section 11). Produced 2026-08-16.

## 1. Candidate bytes

Bundle root `artifacts/BEAAA-23703/`. Recorded hashes recompute with
`sha256sum -c candidate/SHA256SUMS` and `sha256sum -c SHA256SUMS`.

| File | SHA-256 | Installed path | Owner | Mode |
| --- | --- | --- | --- | --- |
| `candidate/backbond-real-agent-demo-ssh-gate` | `cf1cb60324c8ec3ae62a9c818ec42a4098e13a7792372a4651f6a0022f59ca80` | `/usr/local/sbin/backbond-real-agent-demo-ssh-gate` | `root:root` | `0755` |
| `candidate/backbond-real-agent-demo-secret-preflight` | `5e228cb1a66d03d1576d348014aa8644fcf5061f7fa8f5594854b015a20fe914` | `/usr/local/sbin/backbond-real-agent-demo-secret-preflight` | `root:root` | `0755` |
| `candidate/sudoers-backbond-real-agent-demo` | `8544421bd725325d54c61729dabbe0eacf5b0572eaa5f84c351b00dd783304ae` | `/etc/sudoers.d/backbond-real-agent-demo` | `root:root` | `0440` |
| `candidate/install_host_gate.py` | `58a2dd2ae995fd91e3fe7a62e88428687aafe1941de4f421feacecfe5fae452b` | not installed; run once from staging | `root:root` | `0700` |
| `candidate/verify_release_manifest.py` | `4a81161e021ff171480472d76531feeba0d8007e636abda491ed0c370de77dba` | not installed; reviewer tool | n/a | n/a |

The release pin both privileged and unprivileged components enforce:

- SHA `d9d680f53db7de263f026be5284c97229126872c`, the current tip of `origin/master`
- 906 files, no symlinks
- tree SHA-256 `5d6398a3cbf567778a8ca9ae7254b82a286a782dbd2874f0f711e9292bf16c17`

## 2. The exact allowlist

`SSH_ORIGINAL_COMMAND` is compared against a closed set. Four shapes are accepted and the process
exits non-zero on everything else. Three are literal string equality with no globbing and no shell.
Only the deploy shape carries a variable field, and that field is a strict UTC timestamp.

| Accepted `SSH_ORIGINAL_COMMAND` | d9 workflow line | What the gate then does |
| --- | --- | --- |
| `mkdir -p /opt/backbond-real-agent-demo/releases/d9d680f5…872c` | 118 | `os.mkdir` of that one path, mode `0755`. No process is spawned. |
| `scp -t <release dir>[/]`, optionally with `--` | 119 | Single-file legacy sink. Only the member name `real-agent-demo-release.tgz` is accepted. |
| one of six `sftp` subsystem strings | 119 | Single-file write-only SFTP sink whose whole namespace is the pinned archive path. |
| `cd <release dir> && tar -xzf real-agent-demo-release.tgz` | 120 | Member pre-scan, extraction, then the 906-file digest check. |
| `DEMO_GIT_SHA='d9…' DEMO_DEPLOYED_AT='<UTC>' bash '<release dir>/demo-real-agent/deploy.sh' '<release dir>'` | 127 | `execve("/bin/bash", …)` under the deployment account. |

The mkdir, extract and deploy shapes are not hand-written. A test reads
`.github/workflows/real-agent-demo.yml` out of d9 with `git show`, substitutes `${GITHUB_SHA}`, and
asserts the rendered strings resolve. If the workflow text and the gate ever disagree, that test
fails.

The deploy shape is checked in four independent places: the SHA in `DEMO_GIT_SHA`, the SHA inside the
script path, the SHA inside the source-root argument, and the timestamp format. A mismatch between
any of them is refused, so an operator cannot mix a d9 variable with an a68e7e09 path.

Refused input is never echoed back into the host log. The gate prints a fixed reason plus
`rejected_command_sha256=<hex>`, so an operator can correlate an attempt without giving the caller a
way to write chosen text into the log.

## 3. Privilege model

The deployment account gains no new capability, and root never touches repository content.

- The forced command runs as `passive-demo-deploy`. It calls `require_unprivileged()` first and
  refuses to continue if euid or egid is 0.
- Upload, extraction and `deploy.sh` all run unprivileged. The gate contains no call to `sudo`.
- `deploy.sh` at d9 performs the single privileged call itself:
  `sudo --non-interactive /usr/local/sbin/backbond-real-agent-demo-secret-preflight`. That path is
  hard-coded at d9, which is why the helper carries that exact name.
- The helper takes no arguments, reads no caller-supplied state, and executes nothing. It proves the
  protected environment, proves the 906-file tree, then atomically consumes the authorization.
- The container reads the protected environment through the read-only bind mount d9's `deploy.sh`
  already sets up, running as uid 10001, which matches the file owner. That is what removes the
  BEAAA-23669 root cause without giving uid 1002 read access to the secret values.

## 4. The one-shot authorization

`/etc/backbond/real-agent-demo-deploy.allow`, root-owned, mode `0600`, containing exactly the d9
SHA. The helper requires a regular file, uid and gid 0, mode `0600`, and `st_nlink == 1`, then
renames it into `/etc/backbond/real-agent-demo-deploy-consumed/<sha>-<ns>`. `rename` is atomic, so
under a race exactly one caller wins and every other caller sees `ENOENT` and fails.

Verification runs before consumption, so a release that fails the digest check leaves the
authorization unspent. A test asserts exactly that. Once a deployment consumes the token, a repeat
requires a host owner to create a fresh file under a new explicit decision.

The file does not exist and this ticket did not create it. The host is fail-closed today.

## 5. Removing the stale a68e7e09 authority

| Item | Before (BEAAA-23669) | After | Direction |
| --- | --- | --- | --- |
| sudoers | two entries, one ending in a `*` wildcard argument, targeting a root runner that `execve`d `deploy.sh` | one entry, fixed helper, `""` for no arguments | narrower |
| root deploy runner | `/usr/local/sbin/backbond-real-agent-demo-deploy` ran repository content as root | file removed | narrower |
| forced command | fell back to `bash -c "$SSH_ORIGINAL_COMMAND"` for anything unrecognised | no fallback; everything outside the allowlist exits non-zero | narrower |
| SSH key | `restrict,command="…-ssh-gate"`, fingerprint `SHA256:z0GzuYG3rnNtFjMKeOsG36kDWrsXeG0larXTHd9V3Dg` | byte-identical, not rewritten | unchanged |
| sealed directory | `/opt/backbond-real-agent-demo/sealed`, used by the root runner | removed if empty | narrower |
| filesystem | — | adds root-only `…-deploy-consumed/`, mode `0700` | unchanged for uid 1002 |
| Docker, secrets, repository | — | untouched | unchanged |

The previous gate's arbitrary-command fallback was the widest thing on the host, and removing it is
the single largest reduction here. It has a consequence: any workflow step that relied on that
fallback now has to be an allowlisted shape, which is what section 8 is about.

## 6. Tests

`artifacts/BEAAA-23703/tests/test_beaaa_23703_host_gate.py`, 71 tests, all passing:

```
python3 -m pytest artifacts/BEAAA-23703/tests/test_beaaa_23703_host_gate.py -q
71 passed
```

The tests load the candidate bytes unmodified and redirect only path constants. Worth calling out:

- 33 rejection cases, including every a68e7e09 shape the previous gate accepted, mixed-SHA deploy
  lines, an uppercase SHA, `..` in the source root, appended `; id` and `&& id`, a doubled space, a
  trailing space, quoted paths, and a direct call to the privileged helper.
- The real d9 release, materialised with `git read-tree` and `git checkout-index` into a genuine
  checkout, tarred with d9's own argument list, extracted, and measured at 906 files and
  `5d6398a3…`.
- A test asserting the tar argument list matches d9's workflow text, added after the defect in
  section 7.
- A real OpenSSH `scp` client uploading that 5,623,303-byte archive through the SFTP sink, asserting
  `transport=sftp` in the sink's own output so the test cannot pass as a plain local copy.
- The sink refusing a write outside the release directory, and refusing `deploy.sh` inside it, with
  the file confirmed absent afterwards.
- Archives carrying `../escaped-marker` and `/etc/cron.d/backdoor` members, both refused before
  extraction.
- One-shot semantics, second consumption refused, and a bad tree leaving the token unspent.
- `visudo -c` accepting the sudoers file.
- Helper output asserted to contain no environment value, and to emit the exact
  `DEMO_JUDGE_BACKEND: configured` line that d9's `deploy.sh` greps for with `grep -Fxq`.

Independent recomputation, both routes agreeing:

```
python3 artifacts/BEAAA-23703/candidate/verify_release_manifest.py --from-git .
python3 artifacts/BEAAA-23703/candidate/verify_release_manifest.py --from-tree <release dir>
files = 906   tree_sha256 = 5d6398a3cbf567778a8ca9ae7254b82a286a782dbd2874f0f711e9292bf16c17
```

## 7. A defect found and corrected in this ticket

An earlier revision of this candidate pinned 906 as 902 files and the tree digest as
`b6d3402d1d66f2fdf83069e64ac32467d3e442b4448fa36c1bfc6ab50a9c62c8`. The cause was reading
`.github/workflows/real-agent-demo.yml` from the working tree instead of from d9. The working tree
packs a shorter tar list. At d9, line 117 also packs `release`, `release_identity.py` and
`artifacts/beaaa-18720/unified-probe-inventory.jsonl`, which is 2 + 1 + 1 = 4 more files.

The wrong pin would have failed closed rather than deployed something unintended: the gate would
have refused the correct d9 release at the extract step. It would still have blocked the deployment
this programme is trying to complete, and it would have put a number in an attestation that does not
recompute. It is corrected throughout, and
`test_release_roots_come_from_the_d9_workflow_not_the_working_tree` now asserts the tar list against
d9's own text so the same mistake fails loudly.

A second, partly overlapping candidate set was present in the working directory when this run
started, and the wake comment asked for prior scratch to be re-hashed and either proved or
discarded. Its hashes, for the record:

| Prior scratch file | SHA-256 |
| --- | --- |
| `backbond-real-agent-demo-ssh-gate` (variant) | `d2633892437a9a8be099275e19ca333807000611cf662713bae6d8780229953e` |
| `backbond-real-agent-demo-authorize` | `8bd9e2468515814f4ef2eded9f2a8a34ff04d416326e2e0b41cb19d495da5b3e` |
| `backbond-real-agent-demo-authorize.sudoers` | `12ece5c9137747e0166463de09f905895ee076f5280a9618b87edc51328b015b` |
| `verify_release_manifest.py` (variant) | `548a72c8df325a6f77efa906627efd26a3995e79f5c0c885e4e9076ca05eebd8` |
| `tests/test_d9_host_gate.py` | `f5f5fbdc14674b2c19eeda26de7ae987e7c53eb9456384ef83559808bf9a3703` |
| `tests/e2e_forced_command.sh` | `f4ad758e6b048e9b9dbd5ba48846ddd0af2acf98dd6d950c14437e51dda469a9` |
| `SHA256SUMS` (variant) | `b0ab0eb0fcb94e751676d0471a5c5fce88be15bb6955246a2ed590536ac7b527` |

That set carried the correct 906 pin, and its tar list is what exposed the defect above. It was not
adopted, for three reasons that stand independently of the pin. It introduced a second privileged
helper, `backbond-real-agent-demo-authorize`, invoked by the gate through `sudo` in addition to the
`-secret-preflight` helper d9's `deploy.sh` calls, where the brief allows one fixed helper. Its
second sudoers file granted by numeric uid `#1002` rather than by account name, and the rule was
commented out, so it was inert as written and inconsistent with the other sudoers file if enabled.
Its `SHA256SUMS` omitted three of the files it shipped, so the manifest did not cover the bundle.

`tests/e2e_forced_command.sh` from that set is a good idea worth carrying forward and is not
included here. It stands up a real `sshd` with the candidate as the forced command, which is
stronger evidence than anything in section 6. It was excluded because it asserts a denial string
belonging to the variant's design rather than this one, and because it writes to and removes the
real `/opt/backbond-real-agent-demo/releases` path, which is not a side effect this ticket should
have. Adapting it is recommended under BEAAA-23702 where a host-representative environment exists.

## 8. Workflow compatibility, and the upload decision

The mkdir, extract and deploy shapes come from d9's workflow lines 118, 120 and 127 and are asserted
against that file in tests. They will match.

The upload step, line 119, is the open item. It runs a bare
`scp -i ~/.ssh/real-agent-demo real-agent-demo-release.tgz "$SSH_USER@$SSH_HOST:…/"`. Since OpenSSH
9.0, `scp` speaks the SFTP protocol by default and there is no automatic fallback to the legacy
protocol; `-O` is required to force it. On an `ubuntu-latest` runner the upload will therefore
request the `sftp` subsystem, not `scp -t`. The previous gate absorbed this through its
arbitrary-command fallback. This candidate has no fallback, so the upload has to be an explicit
allowlist entry.

Two options were available and the narrower one was taken. Handing the request to a stock
`sftp-server` would give the deploy key an unbounded read and write channel as uid 1002 across the
whole filesystem, which broadens filesystem permissions and contradicts the brief. Instead the gate
implements a write-only SFTP v3 sink whose entire namespace is the single pinned archive path. It
accepts `INIT`, `REALPATH`, `STAT`, `LSTAT`, `FSTAT`, `OPEN`, `WRITE`, `CLOSE` and a no-op
`SETSTAT`/`FSETSTAT`, refuses reads, appends, second opens and every other request, caps the payload
at 2 GiB, and opens with `O_NOFOLLOW`. A real `scp` client exercises it in the test suite.

The legacy `scp -t` shapes are kept as well, so a runner invoking `scp -O` also works.

## 9. What BEAAA-23702 should decide before installing

1. The SFTP sink is roughly 150 lines of new protocol code in the upload path. It runs unprivileged
   and is confined to one filename, but it is the largest new surface in this candidate and deserves
   direct review. The alternative is to refuse `sftp`, which means the d9 workflow cannot upload at
   all without a repository change after d9, and so is not compatible with deploying exact d9.
2. The six accepted subsystem strings cover the common `Subsystem sftp` values. The host's actual
   value was not observed, because this ticket does not touch the host. Confirm it before installing
   with `sshd -T | grep -i subsystem`. If it is not one of the six, the upload fails closed and the
   string must be added to `SFTP_SUBSYSTEM_SHAPES`.
3. The forced-command wiring itself is not proven by section 6. The tests drive the gate's entry
   points directly and drive the SFTP sink with a real client, but no test stands up an `sshd` with
   this file as `command="…"` in `authorized_keys`. Section 7 names the script that would close this
   and why it was not shipped. Treat the end-to-end path as reviewed by construction, not measured.

Every unresolved case fails closed. Nothing here degrades into a wider permission.

## 10. Rollback

The transition replaces two files, installs one, and removes two. `install_host_gate.py` copies
`…-ssh-gate`, the sudoers drop-in and the stale runner into
`/var/backups/backbond-demo/BEAAA-23703/` before touching anything, printing each backup's SHA-256.
It verifies staged bytes against `candidate/SHA256SUMS`, runs `visudo -c` on the candidate sudoers
before installing it, writes through a same-directory temporary file and `os.replace` so no path is
ever partially written, and re-checks the authorized key at the end. It refuses to overwrite an
existing `-secret-preflight`, and refuses to remove a non-empty sealed directory.

To roll back, as root, with no deployment in flight:

```sh
install -o root -g root -m 0755 \
  /var/backups/backbond-demo/BEAAA-23703/backbond-real-agent-demo-ssh-gate.before \
  /usr/local/sbin/backbond-real-agent-demo-ssh-gate
install -o root -g root -m 0440 \
  /var/backups/backbond-demo/BEAAA-23703/backbond-real-agent-demo-deploy.before \
  /etc/sudoers.d/backbond-real-agent-demo
install -o root -g root -m 0755 \
  /var/backups/backbond-demo/BEAAA-23703/backbond-real-agent-demo-deploy.before \
  /usr/local/sbin/backbond-real-agent-demo-deploy
rm -f /usr/local/sbin/backbond-real-agent-demo-secret-preflight
mkdir -m 0755 /opt/backbond-real-agent-demo/sealed
visudo -c
```

No daemon reload is needed. OpenSSH reads `authorized_keys` per connection and sudo reads the
drop-in per invocation. The authorized key is not modified by the transition, so rollback does not
touch it. Rolling back restores the a68e7e09 authority and the arbitrary-command fallback, so treat
it as an incident action rather than a routine one.

## 11. Scope, classification and what was not done

Classification is rule-execution under `contracts/scanner-implementation-supervision-v1.md`. The
candidate changes no Scanner rule, no rubric weight, no dimension, no probe and no scoring path. It
is host access-control bytes for deploying an already-approved release, so there is no golden-set
delta to report and no `rubric_point_id` applies. Recorded as a determination rather than omitted.
The work touches a host privilege boundary, which is why the attestation sits with the CTO on
BEAAA-23702 rather than closing here.

Not done, by instruction: nothing was installed, armed or deployed. No authorization file was
created. No GitHub state was changed and nothing was pushed. No container, image, secret value,
repository variable or SSH key was touched. No secret value was read or printed; the helper proves
the protected environment by metadata and by variable name only.

One deliberate disclosure choice for review: the helper prints `env_sha256`, a SHA-256 over the
protected environment file, into the workflow log. It carries no value and gives operators a
fingerprint to prove the file did not change between deployments. BEAAA-23669 published the same
field. Flagged because the workflow log has a wide audience.
