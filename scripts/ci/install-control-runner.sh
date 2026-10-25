#!/usr/bin/env bash
# Prepare only. Registration and listener activation are separate operations.
# Usage: bash install-control-runner.sh /root/provided/clean-base.tar SHA256
set -euo pipefail
test "$(id -u)" = 0
test "$#" = 2
test "$(uname -m)" = x86_64
. /etc/os-release
test "$ID" = ubuntu && test "$VERSION_ID" = 24.04
test "$(ps -p 1 -o comm= | tr -d ' ')" = systemd
source_dir=$(cd -- "$(dirname -- "$0")" && pwd)
archive=$(realpath -e -- "$1")
test -f "$archive" && test ! -L "$1"
test "$(stat -c '%u:%a' "$archive")" = 0:600
[[ "$2" =~ ^[0-9a-f]{64}$ ]]
printf '%s  %s\n' "$2" "$archive" | sha256sum --check --status
for tool in podman nft loginctl useradd ip python3; do command -v "$tool" >/dev/null; done

account=backbond-ci-control
account_uid=1006
account_home=/var/lib/backbond-ci-control
config_dir=/etc/backbond-ci-control
test ! -e "$account_home" && test ! -e "$config_dir"
! getent passwd "$account" >/dev/null
! getent passwd "$account_uid" >/dev/null
! nft list table inet backbond_ci_control >/dev/null 2>&1
test ! -e /etc/systemd/system/backbond-ci-control-egress.service
test ! -e /etc/systemd/system/user-1006.slice.d/50-backbond-ci-control.conf
test ! -e /etc/systemd/system/user@1006.service.d/50-backbond-ci-control.conf
grep -Eq '^nameserver 127\.0\.0\.53$' /etc/resolv.conf
host_ipv4=$(ip -j -4 address show | python3 -c 'import ipaddress,json,sys; print(", ".join(sorted({a["local"] for i in json.load(sys.stdin) for a in i["addr_info"] if ipaddress.IPv4Address(a["local"]).is_global})))')
test -n "$host_ipv4"

useradd --uid "$account_uid" --create-home --home-dir "$account_home" --shell /usr/sbin/nologin "$account"
chmod 700 "$account_home"
test "$(id -Gn "$account")" = "$account"
# Do not assume useradd's allocated subordinate ranges are disjoint.
python3 - <<'PY'
from pathlib import Path
for filename in ("/etc/subuid", "/etc/subgid"):
    rows = [line.split(":") for line in Path(filename).read_text().splitlines() if line]
    mine = [(int(start), int(start) + int(size)) for user, start, size in rows if user == "backbond-ci-control"]
    assert len(mine) == 1 and mine[0][1] - mine[0][0] >= 65536, filename
    for user, start, size in rows:
        if user != "backbond-ci-control":
            assert max(mine[0][0], int(start)) >= min(mine[0][1], int(start) + int(size)), filename
PY
install -d -m 700 "$config_dir"
cat > "$config_dir/egress.nft" <<NFT
table inet backbond_ci_control {
  chain output {
    type filter hook output priority -10; policy accept;
    meta skuid 1006 ip daddr 127.0.0.53 udp dport 53 accept
    meta skuid 1006 ip daddr 127.0.0.53 tcp dport 53 accept
    meta skuid 1006 meta nfproto ipv6 reject
    meta skuid 1006 ip daddr { $host_ipv4, 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.0.0.0/24, 192.0.2.0/24, 192.168.0.0/16, 198.18.0.0/15, 198.51.100.0/24, 203.0.113.0/24, 224.0.0.0/4, 240.0.0.0/4 } reject
    meta skuid 1006 tcp dport 443 accept
    meta skuid 1006 reject
  }
}
NFT
chmod 600 "$config_dir/egress.nft"
nft --check --file "$config_dir/egress.nft"
cat > /etc/systemd/system/backbond-ci-control-egress.service <<'UNIT'
[Unit]
Description=Private and host network denial for the CI control worker
Before=user@1006.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft --file /etc/backbond-ci-control/egress.nft
[Install]
WantedBy=multi-user.target
UNIT
install -d -m 755 /etc/systemd/system/user-1006.slice.d /etc/systemd/system/user@1006.service.d
cat > /etc/systemd/system/user-1006.slice.d/50-backbond-ci-control.conf <<'UNIT'
[Slice]
MemoryMax=1G
MemorySwapMax=0
CPUQuota=25%
TasksMax=256
UNIT
cat > /etc/systemd/system/user@1006.service.d/50-backbond-ci-control.conf <<'UNIT'
[Unit]
Requires=backbond-ci-control-egress.service
After=backbond-ci-control-egress.service
[Service]
OOMScoreAdjust=1000
UNIT
systemctl daemon-reload
systemctl enable --now backbond-ci-control-egress.service
loginctl enable-linger "$account"
systemctl start user@1006.service
run_control() {
  (cd "$account_home" && runuser -u "$account" -- env HOME="$account_home" \
    XDG_RUNTIME_DIR=/run/user/1006 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1006/bus \
    systemd-run --user --quiet --wait --collect --pipe --service-type=exec \
    --property=KillMode=process \
    --property=OOMScoreAdjust=1000 --working-directory="$account_home" \
    --setenv=HOME="$account_home" --setenv=XDG_RUNTIME_DIR=/run/user/1006 \
    --setenv=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1006/bus -- "$@")
}
# Exported from the protected image-builder store, never from a live runner home.
# Only root reads the archive; the unprivileged loader receives a stream.
cat -- "$archive" | run_control podman load
run_control podman image exists localhost/backbond-ci:2.337.0
run_control podman run --detach --init --name backbond-ci-control \
  --network slirp4netns:allow_host_loopback=false \
  --cpus 0.25 --memory 1g --memory-swap 1g --pids-limit 128 \
  --oom-score-adj 1000 --cap-drop ALL --security-opt no-new-privileges \
  --volume backbond-ci-control-data:/home/backbond-ci:U \
  localhost/backbond-ci:2.337.0
# A detached container/pause helper must survive its transient CLI service.
# Its descendants remain charged to the bounded UID slice after CLI exit.
run_control podman inspect --format '{{.State.Running}}' backbond-ci-control | grep -qx true
run_control podman exec backbond-ci-control sh -c \
  'test "$(id -un)" = backbond-ci; test ! -e .runner; test ! -e .credentials; test -w "$RUNNER_TOOL_CACHE"'
install -m 644 "$source_dir/control_worker_probe.py" "$config_dir/control_worker_probe.py"
run_control podman exec -i backbond-ci-control python3 - --check < "$source_dir/control_worker_probe.py"
printf '%s\n' 'Prepared unregistered control worker. Validate isolation, then register with sole custom label backbond-ci-control. The listener is not started.'
