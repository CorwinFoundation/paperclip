#!/bin/sh
set -eu
test "$(id -u)" -eq 0 || { echo 'run as root' >&2; exit 1; }
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
unit_user=backbond-scan
checkout=/opt/backbond/src
scan_venv=/opt/backbond/scan-venv
manifest_root=/var/lib/backbond/scans

id "$unit_user" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "$unit_user"
test -d "$checkout/.git" || { echo "missing root-owned checkout: $checkout" >&2; exit 1; }
test "$(stat -c %U "$checkout")" = root || { echo "checkout must be root-owned: $checkout" >&2; exit 1; }
git -C "$checkout" fetch --quiet origin master
git -C "$checkout" merge --ff-only --quiet origin/master

command -v python3.11 >/dev/null 2>&1 || {
  echo "python3.11 is required for the hash-frozen runtime" >&2
  exit 1
}
python3.11 -m venv --clear "$scan_venv"
"$scan_venv/bin/python" -m pip install --require-hashes --disable-pip-version-check \
  -r "$checkout/requirements-runtime.txt"
chown -R root:root "$scan_venv"
chmod -R a+rX "$scan_venv"

install -d -m 0755 /var/lib/backbond /usr/local/libexec /etc/backbond /etc/systemd/system /etc/sudoers.d "$manifest_root"
chmod o+x /var/lib/backbond
install -o root -g root -m 0644 "$here/scan-schedule.conf" /etc/backbond/scan-schedule.conf
for unit in backbond-scan-vapi.service backbond-scan-vapi-garak.service backbond-scan-vapi.timer backbond-scan-zoo.service backbond-scan-zoo.timer; do
  install -o root -g root -m 0644 "$here/$unit" "/etc/systemd/system/$unit"
done
chown "$unit_user:$unit_user" "$manifest_root"
chmod 0755 "$manifest_root"
printf '%s\n' "beai-agent ALL=(root) NOPASSWD: /usr/bin/systemctl start backbond-scan-vapi.service, /usr/bin/systemctl stop backbond-scan-vapi.service, /usr/bin/systemctl restart backbond-scan-vapi.service, /usr/bin/systemctl status backbond-scan-vapi.service, /usr/bin/systemctl start backbond-scan-vapi-garak.service, /usr/bin/systemctl stop backbond-scan-vapi-garak.service, /usr/bin/systemctl restart backbond-scan-vapi-garak.service, /usr/bin/systemctl status backbond-scan-vapi-garak.service, /usr/bin/systemctl start backbond-scan-zoo.service, /usr/bin/systemctl stop backbond-scan-zoo.service, /usr/bin/systemctl restart backbond-scan-zoo.service, /usr/bin/systemctl status backbond-scan-zoo.service" > /etc/sudoers.d/backbond-scan-schedule
chmod 0440 /etc/sudoers.d/backbond-scan-schedule
visudo -cf /etc/sudoers.d/backbond-scan-schedule >/dev/null
systemctl daemon-reload
systemctl enable backbond-scan-vapi.timer
# The zoo timer stays installed but disabled until a controlled-target runner
# exists and a zoo manifest shows target_called > 0 (BEAAA-30240).
systemctl disable backbond-scan-zoo.timer
echo "Installed the weekly Vapi timer (enabled) and the nightly zoo timer (disabled). Root command: $here/install.sh"
