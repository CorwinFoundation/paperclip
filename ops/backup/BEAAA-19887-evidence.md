# BEAAA-19887 candidate evidence

## Candidate

- Deployment-file manifest identifier: `2f68d6524efcf0a69b3369792f155b4fde92e3f6ed610cd8a97ff1c874a2db30`.
- Supersedes candidate `b00f07ac4e513e52c91db07c7526d5fde6e1add4041917f12bcb46421543ae76` after repository whitespace validation removed terminal blank lines from the unit, timer, and fixture only. The deployed script remained byte-identical (`0d00dfc37faae1f1348ae8ff1d7baf094d0e4377ac9cbb6472fb6c88ec7adabc`) to the script that passed the manual run.
- Also supersedes pre-run candidate `4532d6cc34899dd2df99fdda5f48538a35fb36f6e5b0a5c4b37b23f6fbef3d08`, whose direct built-CLI invocation failed dependency resolution before producing a backup.
- Running Paperclip release used by the supported CLI: `paperclip-536d32f8d91b625bc6021e82bd9f26174cfb8aad`.
- The live unit/script/timer were not replaced; promotion remains gated on canonical QA approval for this exact candidate.

## Verification

- `bash -n` passed for the backup and fixture scripts.
- `systemd-analyze verify` passed for the candidate service and timer.
- Retention dry-run and mutation fixture passed, including the rule that the newest complete archive is never deleted even when it is older than seven days.
- Manual candidate run completed at `2026-07-31T17:32:09Z`.
- The run created a fresh logical backup through `paperclipai db:backup` before the filesystem archive, with 30-day logical retention supplied to the CLI.
- Fresh logical backup: `paperclip-host-20260731T171249Z-20260731-171253.sql.gz`, 2,075,350,616 bytes; `gzip -t` passed.
- Filesystem archive: `paperclip_mempalace_20260731T171249Z.tar.gz`, SHA-256 `11227ca6f19ff37e454869988c171b1682c9325ba61706f5814ccd86ec207d33`.
- Inventory SHA-256: `e1d69807dea9bea57ca233bc0a936edebefb08113adfbacd422a6443d4ce39c9`.
- `sha256sum -c` passed for both archive and inventory.
- Full archive member listing proved the presence of `config.json`, `secrets/master.key`, local storage, the fresh logical backup, the non-sensitive inventory, and both MemPalace trees.
- The member listing proved `home/beai-agent/.paperclip/instances/default/db/` absent.

## Host evidence

Before the run:

- `/`: 309 GiB total, 87 GiB used, 223 GiB available, 28%.
- `/mnt/paperclipdata`: 100 GiB total, 85 GiB used, 9.1 GiB available, 91%.
- RAM: 15 GiB total, 7.9 GiB available.
- Swap: existing `/swapfile`, 4,294,963,200 bytes total and 4,294,688,768 bytes used.

After the run:

- `/`: 309 GiB total, 86 GiB used, 224 GiB available, 28%.
- `/mnt/paperclipdata`: 100 GiB total, 87 GiB used, 7.2 GiB available, 93%.
- RAM: 15 GiB total, 9.0 GiB available.
- Swap: existing `/swapfile`, 4,294,963,200 bytes total and 4,294,778,880 bytes used.
- Timer remains enabled with next trigger `2026-08-01 03:20:00 UTC`.

The constrained-volume increase is the required fresh logical dump; the 4.1 GiB filesystem archive was correctly written to root. Agent workspaces account for approximately 28 GiB on `/mnt/paperclipdata`, but they were not pruned or moved because current issue/worktree liveness was not proven path by path. No swap was added because 9.0 GiB RAM was available after the run while the existing swap remained saturated; changing swap would not address the constrained data filesystem.

## Rollback

`README.md` provides a QA-gated install that first takes timestamped root-owned copies of the current script/service/timer, then uses same-filesystem atomic renames and `systemctl daemon-reload`. The rollback reverses those renames and never deletes the last known-good logical or filesystem backup.
