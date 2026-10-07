# Host scripts (srv1749248, user `beai-agent`)

Cron-driven and operator-run scripts that run the Paperclip host. They used to
live only in `~/.local/bin`, which is not a git working tree, so the only copy
of each was the installed file plus whatever `.bak-*` sat beside it. Filed as
BEAAA-36015 after the Codex OAuth guard went from covering 1 of 21 seats to
holding fleet auth across four issues in one night with no repo home.

`ops/paperclip/numbat/` already tracks this class of artefact (host shell
scripts plus systemd units), so the location was settled by precedent.

## The rule: the repo is authoritative

Edit the copy here, commit it, then run the installer:

```
ops/paperclip/host-scripts/install-host-scripts.sh
```

Never edit `~/.local/bin/<script>` directly. If an emergency fix goes onto the
box first, copy it back into this directory and commit it in the same sitting —
a fire-fix applied only to the install is not landed.

Enforcement is `check-host-script-drift.sh`: read-only, costs nothing, exits 1
on any drift or absence. Run it from any checkout on any heartbeat that touches
these scripts, and before believing that this directory is the running code.

Why a drift check rather than the guard verifying itself against the repo on
every run: the box has no canonical `are-scanner` checkout on `master`
(`/opt/are-scanner` is not a git repository; every checkout on the box sits on
some agent's feature branch). A cron job that compared the install against "the
repo" would be comparing it against whatever branch an agent happened to leave
behind. The drift check runs where a known-good checkout exists instead.

## Tracked scripts

| Script | Schedule | Owner issue | What it does |
| --- | --- | --- | --- |
| `are-codex-oauth-guard.sh` | `*/5 * * * *` | BEAAA-7616, 36012, 36013, 36014 | Self-heals the `codex_local` OAuth symlinks for all 21 seats. Three-class credential discriminator, destructive repair path. The least reproducible-from-memory script on the box. |
| `paperclip-run-log-retention.sh` | `40 3 * * *` | BEAAA-24540 | Ages out run-log `.ndjson` bodies; the durable record stays in Postgres. |
| `numbat-claude-cohort-scan.py` | `0 * * * *` | BEAAA-24636 | Numbat capture for exactly three authorized Claude-runtime seats. |
| `prune-paperclip-db-backups.sh` | not on cron | — | Backstop prune for `/mnt/paperclipdata/db-backups`; the generator self-prunes at 7 days. Run by hand. |
| `prune-workspace-oci-tars.sh` | not on cron | — | Deletes delivered `*.oci.tar` build artefacts (~922 MB each) from agent workspaces. Run by hand; supports `--dry-run`. |
| `codex-auth-writewatch.sh` | not on cron | BEAAA-35987 | inotify loop recording the *shape* — never the value — of writes to the one shared Codex credential. |
| `codex-auth-writewatch.py` | not on cron | BEAAA-35987 | The Python worker behind the writewatch loop. |
| `backbond-uptime-check.py` | `backbond-uptime-check.timer` (every 5 min, systemd --user) | BEAAA-38869 | Probes scanner release-identity, the scan intake API and backbond.ai/scan/. Two failures in a row → one Telegram to the founder; one "recovered" when back; silent while healthy. State in `~/.local/state/backbond-uptime/`. Enable once: `systemctl --user daemon-reload && systemctl --user enable --now backbond-uptime-check.timer`. |

`manifest.sh` is the single roster the installer and the drift check both read,
so they cannot disagree about what is tracked.

## Declared out of scope

These live in `~/.local/bin` and stay untracked deliberately. Nobody should
treat them as a gap.

- `vba_extract.py` — ships with the `XlsxWriter` package as a console script.
  Third-party, reinstalled by pip, not ours to version.
- `mempalace-trial-snapshot.sh` — **does not exist on the box.** Two crontab
  lines (`10 6` and `10 18`) still invoke it and have been failing silently.
  There is nothing to commit; the fix is deleting the two cron lines. See
  `crontab.backbond-host`.
- Everything else in `~/.local/bin` is a pip/npm console shim or a vendored
  binary (`cosign`, `claude`, `mempalace`, …). Package-manager territory.

## Adding a script

Drop the file here, add one row to `HOST_SCRIPTS` in `manifest.sh`, add a row to
the table above, record the cron line in `crontab.backbond-host` if it has one,
and run the installer. `check-host-script-drift.sh` will report it as `ABSENT`
until the installer has run, which is the intended behaviour.
