# Secret preflight host installation

This repository does not provide a root-executed installer. BEAAA-23672 owns host installation through separately trusted operator commands after PR #199 is merged.

The operator must use `secret-preflight-install-manifest.json` as the placement contract and perform these steps for each artifact:

1. Verify the checked-out commit and each source file SHA-256 against the exact merged commit recorded on BEAAA-23672.
2. Verify `/usr/bin/python3` exists and resolves to a regular root-owned executable through a root-owned symlink chain. Record the resolved path and run the helper's `py_compile` validator with `/usr/bin/python3`.
3. Create a temporary file in the destination directory with a root-controlled `mktemp` template. Copy the verified bytes without executing repository content, then set the manifest UID, GID, and mode on the temporary file.
4. Validate the temporary file. Run `/usr/sbin/visudo -cf <temporary-file>` for the sudoers artifact. Recompute SHA-256 for both temporary files and compare each digest with its verified source digest.
5. Atomically rename each validated temporary file over its final target with root-owned `mv`. A copy or install command whose destination is the final target does not satisfy this contract.
6. Read back each final target with `lstat`, reject symlinks or non-regular files, and verify UID, GID, mode, and SHA-256. Re-run `visudo -cf` on the final sudoers target.

The helper target is `/usr/local/sbin/backbond-real-agent-demo-secret-preflight`. The sudoers target grants UID 1002 passwordless execution of that helper with an explicit empty argument list. No repository script may be invoked as root during installation.
