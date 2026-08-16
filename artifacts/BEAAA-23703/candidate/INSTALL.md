# Installing the BEAAA-23703 host gate

For the host owner, under BEAAA-23705, after exact-byte QA approval on
BEAAA-23704. Installation does not arm and does not deploy.

`MANIFEST.json` is the placement contract and every hash below comes from it.
`install_host_gate.py` implements exactly this procedure; the manual steps are
recorded here so the procedure can be followed and audited without it.

## What changes

Two root-owned files are replaced in place, at their existing fixed paths:

| Target | From | To |
| --- | --- | --- |
| `/usr/local/sbin/backbond-real-agent-demo-ssh-gate` | `44ff5dfa…` (stale, a68-pinned) | `bd564caf…` |
| `/usr/local/sbin/backbond-real-agent-demo-secret-preflight` | `e1d50aec…` (canonical d9) | `927fca8b…` |

Two stale a68 artifacts are removed: `/etc/sudoers.d/backbond-real-agent-demo`
(`37b24501…`) and `/usr/local/sbin/backbond-real-agent-demo-deploy`
(`956065275…`).

Nothing else changes. In particular the single sudoers rule
`/etc/sudoers.d/backbond-real-agent-demo-secret-preflight` (`2a56c29c…`) is
verified and left exactly as it is, so the deployment account keeps exactly one
root action and no permission is broadened. The SSH key and its options, the
protected env file, docker access and the live container are untouched.

## Preconditions

Run every check in `MANIFEST.json` under `preconditions` first. A mismatch is a
stop and a report to BEAAA-23702, not something to fix in passing. In summary:
the `authorized_keys` forced command already points at the gate path; sshd's
`Subsystem sftp` is one of the accepted shapes; `/etc/backbond` is root-owned
and not group or world writable; the protected env file is still
`10001:10001` mode `0600`; the deployment account reaches docker without sudo;
and no arm file or d9 release directory exists.

## Procedure

Either run the installer as root from the candidate directory:

```
python3 install_host_gate.py --verify-only                 # prints the plan, changes nothing
python3 install_host_gate.py --confirm --receipt /root/beaaa-23703-receipt.json
```

or perform the same steps by hand for each target:

1. Verify the candidate source against `MANIFEST.json` with `sha256sum`.
2. Verify the current target against its pinned `replaces_sha256`. Stop on any
   mismatch.
3. Copy the current target into a root-owned `0700` backup directory,
   preserving uid, gid and mode. Record its sha256.
4. Write the reviewed bytes to a temporary file **in the same directory** as the
   target, using a root-controlled `mktemp` template. Do not write to the final
   path.
5. Set uid `0`, gid `0` and the manifest mode on the temporary file, then
   `fsync` it.
6. Validate the temporary file with the manifest's validator:
   `/usr/bin/python3 -m py_compile` for both helpers. `py_compile` compiles and
   does not execute the file. No repository script is ever run as root.
7. Recompute the temporary file's sha256 and compare with the source.
8. `mv` the temporary file over the target. A copy whose destination is the
   final path does not satisfy this contract; the replacement must be an atomic
   rename.
9. Read the final target back with `lstat`, reject symlinks and non-regular
   files, and verify uid, gid, mode and sha256.
10. For each removal, verify the current sha256 against the manifest, back it
    up, unlink it, then re-run `/usr/sbin/visudo -c`.

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

Do not create `/etc/backbond/real-agent-demo-deploy.allow`. Arming is a separate
operator action after BEAAA-23702 returns READY, and `install_host_gate.py`
refuses to run while an arm file exists.

## Rollback

```
python3 install_host_gate.py --rollback --confirm
```

This restores every backed-up target by the same same-directory atomic rename.
Restoring `/usr/local/sbin/backbond-real-agent-demo-secret-preflight` to the
backed-up `e1d50aec…` bytes on its own removes the one-shot behaviour and returns
the host to the canonical d9 preflight, which is the safe resting state. Restore
the a68 sudoers drop-in and deploy wrapper only if a return to the stale posture
is explicitly decided, then re-run `/usr/sbin/visudo -c`.

Removing the arm file, if one was ever created, closes the deployment path in
every case.
