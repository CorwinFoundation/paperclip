#!/usr/bin/env bash
# Prepare a rootless container on the selected Ubuntu 24.04 x64 host.
# Registration is separate and requires a fresh GitHub token. No host service
# is stopped, no ports are published and no host directories enter the container.
set -euo pipefail
test "$(id -u)" = 0
test "$(uname -m)" = x86_64
. /etc/os-release
test "$ID" = ubuntu && test "$VERSION_ID" = 24.04
test "$(ps -p 1 -o comm= | tr -d ' ')" = systemd
source_dir=$(cd -- "$(dirname -- "$0")" && pwd)
test -f "$source_dir/Containerfile"
runner_user=backbond-ci
runner_root=/var/lib/backbond-ci
if getent passwd "$runner_user" >/dev/null || test -e "$runner_root"; then
  echo 'Dedicated runner account/path already exists; inspect before changing it.' >&2
  exit 1
fi

apt-get update
apt-get install -y podman slirp4netns uidmap fuse-overlayfs dbus-user-session
useradd --create-home --home-dir "$runner_root" --shell /usr/sbin/nologin "$runner_user"
chmod 700 "$runner_root"
runner_uid=$(id -u "$runner_user")
test "$(id -Gn "$runner_user")" = "$runner_user"
grep -q "^${runner_user}:" /etc/subuid
grep -q "^${runner_user}:" /etc/subgid
loginctl enable-linger "$runner_user"
systemctl start "user@${runner_uid}.service"
run_ci() {
  cd "$runner_root"
  runuser -u "$runner_user" -- env HOME="$runner_root" \
    XDG_RUNTIME_DIR="/run/user/$runner_uid" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$runner_uid/bus" "$@"
}
install -d -m 700 -o "$runner_user" -g "$runner_user" "$runner_root/build"
install -m 600 -o "$runner_user" -g "$runner_user" "$source_dir/Containerfile" "$runner_root/build/Containerfile"
run_ci podman build --tag localhost/backbond-ci:2.337.0 "$runner_root/build"
run_ci podman run --detach --init --name backbond-ci \
  --network slirp4netns:allow_host_loopback=false \
  --cpus 1.5 --memory 5g --memory-swap 5g --pids-limit 512 \
  --cap-drop ALL --security-opt no-new-privileges \
  --volume backbond-ci-data:/home/backbond-ci:U \
  localhost/backbond-ci:2.337.0
run_ci podman exec backbond-ci sh -c 'id; test "$(id -un)" = backbond-ci; test -w "$RUNNER_TOOL_CACHE"; test ! -r /root/.ssh/id_ed25519'
echo 'Prepared container. Verify host services are unreachable, then register with GitHub.'
