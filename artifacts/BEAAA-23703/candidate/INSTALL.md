# Installing the BEAAA-23703 host gate

For the host owner, under BEAAA-23705, after exact-byte QA approval on
BEAAA-23704. Installation does not arm and does not deploy.

This candidate ships **no installer**. Per the CTO ruling on BEAAA-23726, no
repository-provided program runs as root. The host owner performs the fixed
administrative operations below; the only programs that run as root are system
programs (`mkdir`, `cp`, `mktemp`, `chown`, `chmod`, `mv`, `rm`, `sha256sum`,
`stat`, `find`, `sync`, `/usr/bin/python3` in non-executing syntax-check mode,
and `/usr/sbin/visudo`). The reviewed candidate bytes are copied, never
executed.

`MANIFEST.json` is the placement contract and every hash below comes from it.

## What changes

Two root-owned files are replaced in place, at their existing fixed paths:

| Target | From | To |
| --- | --- | --- |
| `/usr/local/sbin/backbond-real-agent-demo-secret-preflight` | `e1d50aec…` (canonical d9) | `927fca8b…` |
| `/usr/local/sbin/backbond-real-agent-demo-ssh-gate` | `44ff5dfa…` (stale, a68-pinned) | `bd564caf…` |

Two stale a68 artifacts are removed: `/etc/sudoers.d/backbond-real-agent-demo`
(`37b24501…`) and `/usr/local/sbin/backbond-real-agent-demo-deploy`
(`956065275…`).

Nothing else changes. In particular the single sudoers rule
`/etc/sudoers.d/backbond-real-agent-demo-secret-preflight` (`2a56c29c…`) is
verified and left exactly as it is, so the deployment account keeps exactly one
root action and no permission is broadened. The SSH key and its options, the
protected env file, docker access and the live container are untouched.

The order below narrows the host monotonically: the stale a68 sudo authority and
the root deploy wrapper are removed **before** either replacement, so any abort
part-way through leaves a host with strictly fewer privileges than it started
with, never a mixed state that is weaker.

## Preconditions

Run every check in `MANIFEST.json` under `preconditions` first. A mismatch is a
stop and a report to BEAAA-23702, not something to fix in passing. In summary:
the `authorized_keys` forced command already points at the gate path; sshd's
`Subsystem sftp` is one of the accepted shapes; `/etc/backbond` is root-owned
and not group or world writable; the protected env file is still
`10001:10001` mode `0600`; the deployment account reaches docker without sudo;
and no arm file or d9 release directory exists.

## Procedure

Run as root, from the candidate directory, in one shell. `HOSTROOT` is empty for
a real installation; it exists only so this exact text can be rehearsed against a
fake host root. Every step is a fixed administrative operation on a named path.

<!-- BEGIN:install-procedure -->
```sh
set -eu

HOSTROOT="${HOSTROOT:-}"                  # empty on the real host
CAND="${CAND:-$PWD}"                      # reviewed candidate directory
CHOWN="${CHOWN:-/usr/bin/chown}"
VISUDO="${VISUDO:-/usr/sbin/visudo}"

SBIN="${HOSTROOT}/usr/local/sbin"
PREFLIGHT="${SBIN}/backbond-real-agent-demo-secret-preflight"
SSHGATE="${SBIN}/backbond-real-agent-demo-ssh-gate"
A68_SUDOERS="${HOSTROOT}/etc/sudoers.d/backbond-real-agent-demo"
A68_DEPLOY="${SBIN}/backbond-real-agent-demo-deploy"
KEEP_SUDOERS="${HOSTROOT}/etc/sudoers.d/backbond-real-agent-demo-secret-preflight"

NEW_PREFLIGHT=927fca8b796e7e868a319ab2fffb4372e34bb29228723090f31f6b994a46f6f8
NEW_SSHGATE=bd564caf524ccec27cdf00d1b85e7ef18cadbcdc2e4163a605565c19578cab93
OLD_PREFLIGHT=e1d50aec40829636e2ab68488c8887d1bd0e796c8aef9b62f2b66a07445f1542
OLD_SSHGATE=44ff5dfa0faac3174bc97b0546a51194a16ce2fac40ac15f9da984460d5d323f
OLD_A68_SUDOERS=37b24501a7470ba37e2002d6f0a219c3b2222f82a6ca559fd71c3cefc73c02bf
OLD_A68_DEPLOY=956065275defd2abcc95a7d2e14d8e70ee54c10aa799578aa00602f29c9af723
KEEP_SUDOERS_SHA=2a56c29ce2f315d29816bbb7a7ce035ed69d93c4d7531829f06b2ed5a37e0407

stop() { echo "STOP: $1" >&2; echo "run the rollback procedure, then attest on BEAAA-23705" >&2; exit 1; }

verify() {  # verify <path> <expected sha256>
  test -f "$1" || stop "missing $1"
  test ! -L "$1" || stop "$1 is a symlink"
  echo "$2  $1" | sha256sum -c - >/dev/null 2>&1 || stop "unexpected bytes at $1"
}

no_cache_residue() {  # no_cache_residue <directory>
  found="$(find "$1" -maxdepth 1 \( -name '__pycache__' -o -name '*.pyc' \) -print)"
  test -z "$found" || stop "cache residue beside a helper: $found"
}

# 1. a fresh, unique, previously absent root-owned 0700 backup directory.
#    mkdir without -p is the fail-if-exists operation: a second attempt that
#    reaches an existing directory stops here instead of overwriting a
#    rollback set. Record BK; the rollback procedure needs it.
STAMP="${STAMP:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
BK="${HOSTROOT}/root/beaaa-23703-backup-${STAMP}"
mkdir -m 0700 "$BK" || stop "backup directory $BK already exists; choose a new STAMP"
$CHOWN 0:0 "$BK"
echo "backup_directory=$BK"

# 2. verify the reviewed source bytes.
verify "$CAND/backbond-real-agent-demo-secret-preflight" "$NEW_PREFLIGHT"
verify "$CAND/backbond-real-agent-demo-ssh-gate" "$NEW_SSHGATE"

# 3. verify every current target, including the rule that must not change.
verify "$PREFLIGHT" "$OLD_PREFLIGHT"
verify "$SSHGATE" "$OLD_SSHGATE"
verify "$A68_SUDOERS" "$OLD_A68_SUDOERS"
verify "$A68_DEPLOY" "$OLD_A68_DEPLOY"
verify "$KEEP_SUDOERS" "$KEEP_SUDOERS_SHA"

# 4. back up all four affected paths into the unique directory, path-preserving
#    (never by basename), preserving uid, gid and mode, and record the hashes.
for T in "$PREFLIGHT" "$SSHGATE" "$A68_SUDOERS" "$A68_DEPLOY"; do
  REL="${T#"$HOSTROOT"}"
  mkdir -p "$BK$(dirname "$REL")"
  cp -a "$T" "$BK$REL"
done
( cd "$BK" && find . -type f ! -name BACKUP.sha256 | sort | xargs -r sha256sum ) > "$BK/BACKUP.sha256"
chmod 0600 "$BK/BACKUP.sha256"
cat "$BK/BACKUP.sha256"

# 5. remove the stale a68 sudo authority first, then re-parse sudoers.
rm -f "$A68_SUDOERS"
test ! -e "$A68_SUDOERS" || stop "$A68_SUDOERS still present"
$VISUDO -c || stop "sudoers does not parse after removing the stale drop-in"

# 6. remove the stale root deploy wrapper.
rm -f "$A68_DEPLOY"
test ! -e "$A68_DEPLOY" || stop "$A68_DEPLOY still present"

# 7. replace each helper: stage in the same directory, own it, validate it
#    without executing it, compare bytes, then rename atomically and re-attest.
replace() {  # replace <source> <target> <expected sha256>
  DIR="$(dirname "$2")"
  TMP="$(mktemp "$DIR/.backbond-install.XXXXXXXX")"
  cat "$1" > "$TMP"
  $CHOWN 0:0 "$TMP"
  chmod 0755 "$TMP"
  sync "$TMP"
  /usr/bin/python3 -c 'import ast,sys; ast.parse(open(sys.argv[1],"rb").read())' "$TMP" \
    || { rm -f "$TMP"; stop "$1 failed syntax validation"; }
  echo "$3  $TMP" | sha256sum -c - >/dev/null 2>&1 || { rm -f "$TMP"; stop "staged bytes for $2 do not match $3"; }
  mv -f "$TMP" "$2"
  test ! -L "$2" || stop "$2 is a symlink after replacement"
  test -f "$2" || stop "$2 is not a regular file after replacement"
  echo "$3  $2" | sha256sum -c - >/dev/null 2>&1 || stop "$2 does not hold $3 after replacement"
  no_cache_residue "$DIR"
  stat -c 'installed %n uid=%u gid=%g mode=%a' "$2"
}
replace "$CAND/backbond-real-agent-demo-secret-preflight" "$PREFLIGHT" "$NEW_PREFLIGHT"
replace "$CAND/backbond-real-agent-demo-ssh-gate" "$SSHGATE" "$NEW_SSHGATE"

# 8. final state.
no_cache_residue "$SBIN"
verify "$KEEP_SUDOERS" "$KEEP_SUDOERS_SHA"
$VISUDO -c || stop "sudoers does not parse at the end of installation"
echo "install_complete backup_directory=$BK"
```
<!-- END:install-procedure -->

`/usr/bin/python3 -c 'import ast,sys; ast.parse(open(sys.argv[1],"rb").read())'` is
the validator named in `MANIFEST.json`, character for character. It parses the
staged file and does not import, execute or byte-compile it, so it writes no
`__pycache__` directory and no `.pyc` file anywhere. `py_compile` is not used:
it would leave cache bytes beside the target. Step 7 asserts no cache residue
beside each helper immediately after replacement, and step 8 asserts it again for
the directory as a whole.

## If any step fails

The procedure stops at the first failure, by `set -eu` and by `stop()`. On any
non-zero exit:

1. **STOP.** Do not continue, do not retry the remaining steps, do not arm, do
   not deploy. The host is left with fewer privileges than it started with, never
   more, because the removals precede the replacements.
2. Run the rollback procedure below with `BK` set to the backup directory printed
   in step 1.
3. Confirm `/usr/sbin/visudo -c` parses and that the stale a68 sudoers drop-in and
   root deploy wrapper are still absent.
4. Attest the final state on BEAAA-23705 with the hashes the rollback prints, and
   report the failing step to BEAAA-23702. A failed installation is a stop and a
   report, not something to work around on the host.

## After installation

Run the `post_install_verification` checks in `MANIFEST.json` and record the
results on BEAAA-23705:

- `runuser -u passive-demo-deploy -- sudo -n /usr/local/sbin/backbond-real-agent-demo-secret-preflight`
  must print `secret_file: valid` and the `<NAME>: configured` lines on stdout,
  then fail with `deploy_authorization: absent` and exit status 1. **Exit 1 here
  is the correct result**, because nothing is armed. It proves the secret
  boundary still works and that deployment is closed.
- `sudo -l -U passive-demo-deploy` must show exactly one command, the fixed
  helper with an empty argument list, and no a68 deploy entry.
- `/usr/sbin/visudo -c` must parse.
- Re-attest both installed files: `root:root`, `0755`, and the sha256 values
  above.
- `find /usr/local/sbin -maxdepth 1 \( -name '__pycache__' -o -name '*.pyc' \) -print`
  must print nothing.

Do not create `/etc/backbond/real-agent-demo-deploy.allow`. Arming is a separate
operator action after BEAAA-23702 returns READY.

## Rollback

Bounded by design: it restores **only the two replacement targets** and never
recreates the stale a68 authority.

<!-- BEGIN:rollback-procedure -->
```sh
set -eu

HOSTROOT="${HOSTROOT:-}"
BK="${BK:?set BK to the backup directory printed by the installation attempt}"
CHOWN="${CHOWN:-/usr/bin/chown}"
VISUDO="${VISUDO:-/usr/sbin/visudo}"

SBIN="${HOSTROOT}/usr/local/sbin"

stop() { echo "STOP: $1" >&2; exit 1; }

test -d "$BK" || stop "no backup directory at $BK"

# 1. restore only the two replacement targets, by the same same-directory
#    atomic rename. Nothing else in the backup set is restored.
for REL in /usr/local/sbin/backbond-real-agent-demo-secret-preflight \
           /usr/local/sbin/backbond-real-agent-demo-ssh-gate; do
  SAVED="$BK$REL"
  TARGET="${HOSTROOT}$REL"
  test -f "$SAVED" || stop "backup set has no $REL"
  TMP="$(mktemp "$(dirname "$TARGET")/.backbond-rollback.XXXXXXXX")"
  cat "$SAVED" > "$TMP"
  $CHOWN 0:0 "$TMP"
  chmod 0755 "$TMP"
  sync "$TMP"
  mv -f "$TMP" "$TARGET"
done

# 2. the stale a68 authority stays removed. This procedure has no step that
#    recreates it; returning to the a68 posture would be a new privileged
#    change and must go back through BEAAA-23702.
for REL in /etc/sudoers.d/backbond-real-agent-demo \
           /usr/local/sbin/backbond-real-agent-demo-deploy; do
  test ! -e "${HOSTROOT}$REL" || stop "$REL is present; rollback never restores it"
done

# 3. re-parse sudoers and re-attest the final state.
$VISUDO -c || stop "sudoers does not parse after rollback"
rm -f "${HOSTROOT}/etc/backbond/real-agent-demo-deploy.allow"
find "$SBIN" -maxdepth 1 \( -name '__pycache__' -o -name '*.pyc' \) -print
sha256sum "${SBIN}/backbond-real-agent-demo-secret-preflight" "${SBIN}/backbond-real-agent-demo-ssh-gate"
stat -c 'restored %n uid=%u gid=%g mode=%a' \
  "${SBIN}/backbond-real-agent-demo-secret-preflight" "${SBIN}/backbond-real-agent-demo-ssh-gate"
echo "rollback_complete a68_sudoers=absent a68_deploy_wrapper=absent"
```
<!-- END:rollback-procedure -->

Restoring the helper to the backed-up `e1d50aec…` bytes removes the one-shot
behaviour and returns the host to the canonical d9 preflight, which is the safe
resting state. Restoring the gate to `44ff5dfa…` returns the key to a forced
command pinned to a68, which denies every d9 command. Both are narrower than the
installed state, and neither restores the removed a68 sudo authority.

Removing the arm file, if one was ever created, closes the deployment path in
every case.
