#!/bin/sh
set -eu
test "$(id -u)" -eq 0 || { echo 'run as root' >&2; exit 1; }
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
unit_user=are-deploy
checkout=/home/$unit_user/are-scanner
manifest_root=/var/lib/backbond/scans

id "$unit_user" >/dev/null 2>&1
test -d "$checkout/.git" || {
  install -d -o "$unit_user" -g "$unit_user" -m 0755 /home/$unit_user
  su -s /bin/sh -c "git clone --origin origin https://github.com/CorwinFoundation/are-scanner.git '$checkout'" "$unit_user"
}
test "$(stat -c %U "$checkout")" = "$unit_user"
su -s /bin/sh -c "git -C '$checkout' fetch --quiet origin master && git -C '$checkout' merge --ff-only --quiet origin/master" "$unit_user"

install -d -m 0755 /usr/local/libexec /etc/backbond /etc/systemd/system /etc/sudoers.d "$manifest_root"
install -o root -g root -m 0644 "$here/scan-schedule.conf" /etc/backbond/scan-schedule.conf
for unit in backbond-scan-vapi.service backbond-scan-vapi.timer backbond-scan-zoo.service backbond-scan-zoo.timer; do
  install -o root -g root -m 0644 "$here/$unit" "/etc/systemd/system/$unit"
done
chown "$unit_user:$unit_user" "$manifest_root"
chmod 0755 "$manifest_root"
printf '%s\n' "$unit_user ALL=(root) NOPASSWD: /usr/bin/systemctl start backbond-scan-vapi.service, /usr/bin/systemctl stop backbond-scan-vapi.service, /usr/bin/systemctl restart backbond-scan-vapi.service, /usr/bin/systemctl status backbond-scan-vapi.service, /usr/bin/systemctl start backbond-scan-zoo.service, /usr/bin/systemctl stop backbond-scan-zoo.service, /usr/bin/systemctl restart backbond-scan-zoo.service, /usr/bin/systemctl status backbond-scan-zoo.service" > /etc/sudoers.d/backbond-scan-schedule
chmod 0440 /etc/sudoers.d/backbond-scan-schedule
visudo -cf /etc/sudoers.d/backbond-scan-schedule >/dev/null
systemctl daemon-reload
systemctl enable backbond-scan-vapi.timer backbond-scan-zoo.timer
echo "Installed scheduled scans. Root command: $here/install.sh"
