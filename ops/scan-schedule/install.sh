#!/bin/sh
set -eu
test "$(id -u)" -eq 0 || { echo 'run as root' >&2; exit 1; }
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
install -d -m 0755 /usr/local/libexec /etc/systemd/system /etc/sudoers.d /var/lib/backbond/scans
install -o root -g root -m 0755 "$here/backbond-scan-schedule-update" /usr/local/libexec/backbond-scan-schedule-update
for unit in backbond-scan-vapi.service backbond-scan-vapi.timer backbond-scan-zoo.service backbond-scan-zoo.timer; do
  install -o root -g root -m 0644 "$here/$unit" "/etc/systemd/system/$unit"
done
printf '%s\n' 'are-deploy ALL=(root) NOPASSWD: /usr/local/libexec/backbond-scan-schedule-update' > /etc/sudoers.d/backbond-scan-schedule
chmod 0440 /etc/sudoers.d/backbond-scan-schedule
visudo -cf /etc/sudoers.d/backbond-scan-schedule >/dev/null
systemctl daemon-reload
echo 'Installed. Enable only the timer appropriate to this host.'
