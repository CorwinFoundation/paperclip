#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 || $# -ne 0 ]]; then
  echo "usage: run install_secret_preflight.sh as root with no arguments" >&2
  exit 2
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
HELPER_SOURCE="$SCRIPT_DIR/host/real_agent_demo_secret_preflight.py"
SUDOERS_SOURCE="$SCRIPT_DIR/host/backbond-real-agent-demo-secret-preflight.sudoers"
HELPER_TARGET=/usr/local/sbin/backbond-real-agent-demo-secret-preflight
SUDOERS_TARGET=/etc/sudoers.d/backbond-real-agent-demo-secret-preflight

for source in "$HELPER_SOURCE" "$SUDOERS_SOURCE"; do
  if [[ ! -f "$source" || -L "$source" ]]; then
    echo "installer source is missing or unsafe" >&2
    exit 1
  fi
done

install -o root -g root -m 0755 "$HELPER_SOURCE" "$HELPER_TARGET"
sudoers_tmp="$(mktemp /etc/sudoers.d/.backbond-real-agent-demo-secret-preflight.XXXXXX)"
trap 'rm -f "$sudoers_tmp"' EXIT
install -o root -g root -m 0440 "$SUDOERS_SOURCE" "$sudoers_tmp"
visudo -cf "$sudoers_tmp" >/dev/null
mv -f "$sudoers_tmp" "$SUDOERS_TARGET"
chown root:root "$SUDOERS_TARGET"
chmod 0440 "$SUDOERS_TARGET"
visudo -cf "$SUDOERS_TARGET" >/dev/null
trap - EXIT

echo "installed fixed secret preflight helper and sudoers scope"
