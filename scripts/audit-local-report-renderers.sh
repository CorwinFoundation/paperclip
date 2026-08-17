#!/bin/sh
set -eu

# BEAAA-21634: inventory local report_card/render.py copies without mutating or
# deleting worktrees. Every non-allowlisted copy is historical evidence only.

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
allowlist=${SCANNER_RENDERER_ALLOWLIST:-"$repo_root/config/canonical-report-renderers.txt"}
search_root=${SCANNER_RENDERER_SEARCH_ROOT:-/mnt/paperclipdata/dot-paperclip/instances/default/workspaces}

if [ ! -r "$allowlist" ]; then
  echo "ERROR: renderer allowlist is not readable: $allowlist" >&2
  exit 2
fi

is_allowlisted() {
  candidate=$1
  while IFS= read -r entry || [ -n "$entry" ]; do
    case "$entry" in
      ''|'#'*) continue ;;
    esac
    [ "$candidate" = "${entry%/}" ] && return 0
  done < "$allowlist"
  return 1
}

printf 'classification\tbrand_verifier\trepository_root\trenderer_sha256\n'
{
  while IFS= read -r entry || [ -n "$entry" ]; do
    case "$entry" in
      ''|'#'*) continue ;;
    esac
    [ -f "${entry%/}/report_card/render.py" ] && printf '%s\n' "${entry%/}/report_card/render.py"
  done < "$allowlist"
  find "$search_root" -type f -path '*/report_card/render.py' -print 2>/dev/null
} | sort -u | while IFS= read -r renderer; do
  root=${renderer%/report_card/render.py}
  resolved=${root%/}
  if is_allowlisted "$resolved"; then
    classification=CANONICAL
  else
    classification=QUARANTINED_INTERNAL_HISTORICAL_EVIDENCE_ONLY
  fi
  if [ -f "$resolved/scripts/verify-brand-backbone.sh" ]; then
    verifier=present
  else
    verifier=missing
  fi
  digest=$(sha256sum "$renderer" | awk '{print $1}')
  printf '%s\t%s\t%s\t%s\n' "$classification" "$verifier" "$resolved" "$digest"
done
