#!/usr/bin/env bash
# Single source of truth for the host scripts this directory tracks.
# Sourced by install-host-scripts.sh and check-host-script-drift.sh so the
# installer and the drift detector can never disagree about the roster.
#
# Fields per row, tab-free, space-separated:
#   <tracked filename> <install directory> <cron schedule or "-">
#
# INSTALL_ROOT lets a caller stage an install somewhere other than the real
# target (BEAAA-33051: a staged install proves the installer without touching
# the live box; DRY_RUN printing does not).

HOST_SCRIPTS_INSTALL_DIR="${BACKBOND_HOST_SCRIPTS_DIR:-/home/beai-agent/.local/bin}"

HOST_SCRIPTS=(
  "are-codex-oauth-guard.sh|*/5 * * * *"
  "paperclip-run-log-retention.sh|40 3 * * *"
  "numbat-claude-cohort-scan.py|0 * * * *"
  "prune-paperclip-db-backups.sh|-"
  "prune-workspace-oci-tars.sh|-"
  "codex-auth-writewatch.sh|-"
  "codex-auth-writewatch.py|-"
)

host_script_names() {
  local row
  for row in "${HOST_SCRIPTS[@]}"; do printf '%s\n' "${row%%|*}"; done
}
