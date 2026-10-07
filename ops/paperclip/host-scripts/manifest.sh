#!/usr/bin/env bash
# Single source of truth for the host scripts this directory tracks.
# Sourced by install-host-scripts.sh and check-host-script-drift.sh so the
# installer and the drift detector can never disagree about the roster.
#
# Fields per row, tab-free, space-separated:
#   <tracked filename>|<how it is triggered>
#
# The trigger field is documentation, but it is load-bearing documentation: it is
# the only place a reader learns what runs a script. "-" used to mean "no cron
# entry", which reads as "nothing runs this" -- and on BEAAA-36045 that inference
# was actually made about a script supervised by a systemd --user unit. Say the
# mechanism explicitly instead: a cron expression, "systemd:<unit>", or
# "manual" when a human genuinely invokes it.
#
# INSTALL_ROOT lets a caller stage an install somewhere other than the real
# target (BEAAA-33051: a staged install proves the installer without touching
# the live box; DRY_RUN printing does not).

HOST_SCRIPTS_INSTALL_DIR="${BACKBOND_HOST_SCRIPTS_DIR:-/home/beai-agent/.local/bin}"

# systemd --user units that supervise the scripts above. Tracked for the same
# reason the scripts are: a version-controlled script whose supervisor lives only
# on the box is the asymmetry BEAAA-36015 was filed about, one file over. Needs no
# root -- these are the agent user's own units, and `loginctl` linger is on.
HOST_USER_UNITS_INSTALL_DIR="${BACKBOND_HOST_USER_UNITS_DIR:-/home/beai-agent/.config/systemd/user}"

HOST_SCRIPTS=(
  "are-codex-oauth-guard.sh|*/5 * * * *"
  "paperclip-run-log-retention.sh|40 3 * * *"
  "numbat-claude-cohort-scan.py|0 * * * *"
  "prune-paperclip-db-backups.sh|manual"
  "prune-workspace-oci-tars.sh|manual"
  "codex-auth-writewatch.sh|manual (superseded by the .py under systemd)"
  "codex-auth-writewatch.py|systemd:codex-auth-writewatch.service"
  "backbond-uptime-check.py|systemd:backbond-uptime-check.timer"
)

HOST_USER_UNITS=(
  "codex-auth-writewatch.service"
  "backbond-uptime-check.service"
  "backbond-uptime-check.timer"
)

host_script_names() {
  local row
  for row in "${HOST_SCRIPTS[@]}"; do printf '%s\n' "${row%%|*}"; done
}

host_user_unit_names() {
  local row
  for row in "${HOST_USER_UNITS[@]}"; do printf '%s\n' "${row%%|*}"; done
}
