#!/bin/sh
set -eu

action=${1:-}
[ "$#" -eq 1 ] && { [ "$action" = install ] || [ "$action" = uninstall ]; } || {
  echo "usage: $0 install|uninstall" >&2
  exit 2
}

root=${BACKBOND_INSTALL_ROOT:-}
if [ "$(id -u)" -ne 0 ] && [ -z "$root" ]; then
  echo "run as root" >&2
  exit 1
fi

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
helper="$root/usr/local/sbin/qa-intake-counts"
sudoers="$root/etc/sudoers.d/backbond-qa-intake-counts"

if [ "$action" = uninstall ]; then
  rm -f -- "$helper" "$sudoers"
  echo "qa-intake-counts uninstalled"
  exit 0
fi

install -d -m 0755 "$(dirname "$helper")" "$(dirname "$sudoers")"
if [ -n "$root" ]; then
  install -m 0755 "$here/qa-intake-counts.py" "$helper"
else
  install -o root -g root -m 0755 "$here/qa-intake-counts.py" "$helper"
fi

staged=$(mktemp)
trap 'rm -f "$staged"' EXIT HUP INT TERM
printf '%s\n' 'beai-agent ALL=(root) NOPASSWD: /usr/local/sbin/qa-intake-counts *' >"$staged"
chmod 0440 "$staged"
if [ -z "$root" ]; then
  visudo -cf "$staged" >/dev/null
fi
if [ -n "$root" ]; then
  install -m 0440 "$staged" "$sudoers"
else
  install -o root -g root -m 0440 "$staged" "$sudoers"
fi
echo "qa-intake-counts installed"
