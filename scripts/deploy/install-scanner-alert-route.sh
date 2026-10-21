#!/bin/sh
# BEAAA-42989: run once as root on srv1749248. Safe to repeat.
set -eu
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 -c 'import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text())' "$base/backbond-paperclip-alert.py"
getent group systemd-journal >/dev/null
id are-deploy >/dev/null
# No unit restart is needed: the existing OnFailure unit invokes this path.
install -o root -g root -m 0755 "$base/backbond-paperclip-alert.py" /usr/local/sbin/backbond-paperclip-alert.new
mv -f /usr/local/sbin/backbond-paperclip-alert.new /usr/local/sbin/backbond-paperclip-alert
usermod -a -G systemd-journal are-deploy
printf '%s\n' 'Scanner alerts now route to BEAAA-42989. Reconnect are-deploy for journal access.'
