#!/usr/bin/env bash
# Provision only. Does not enable the broker, change runner labels or CI opt-ins.
set -euo pipefail
test "$(id -u)" = 0
test "$(uname -m)" = x86_64
. /etc/os-release
test "$ID" = ubuntu && test "$VERSION_ID" = 24.04
source_dir=$(cd -- "$(dirname -- "$0")" && pwd)
command -v podman
command -v nft
test ! -e /opt/backbond-build-pool
test ! -e /etc/backbond-build-pool
for role in build publish image-build; do
  account=backbond-$role
  account_home=/var/lib/$account
  if getent passwd "$account" >/dev/null; then
    # The build account can already exist from the unregistered Docker probe.
    test "$role" = build
    test "$(getent passwd "$account" | cut -d: -f6)" = "$account_home"
    test "$(id -Gn "$account")" = "$account"
  else
    test ! -e "$account_home"
    useradd --create-home --home-dir "$account_home" --shell /usr/sbin/nologin "$account"
  fi
  chmod 700 "$account_home"
  account_uid=$(id -u "$account")
  test "$account_uid" -gt 1002
  grep -q "^$account:" /etc/subuid
  grep -q "^$account:" /etc/subgid
  loginctl enable-linger "$account"
  systemctl start "user@$account_uid.service"
  systemctl set-property "user-$account_uid.slice" MemoryMax=5G CPUQuota=150% TasksMax=2300
  if [ "$role" != image-build ]; then
    # Job-side Podman uses a private per-job HOME and runtime. No persistent
    # user-service/configuration directory may be writable by an execution UID.
    chown -R root:root "$account_home"
    chmod 711 "$account_home"
  fi
done

install -d -m 755 /opt/backbond-build-pool
install -m 644 "$source_dir"/build_pool_{broker,worker,policy}.py /opt/backbond-build-pool/
install -d -m 700 /etc/backbond-build-pool
install -d -m 711 /var/lib/backbond-build-pool
cat > /etc/backbond-build-pool/config.example.json <<'JSON'
{"app_id":0,"installation_id":0,"key":"/etc/backbond-build-pool/app.pem","egress_sha256":"SET_AFTER_VALIDATING_ACTIVE_EGRESS_RULES"}
JSON
chmod 600 /etc/backbond-build-pool/config.example.json

# Only the new CI host UIDs are affected. Block all host addresses and private
# networks, including metadata and the VM's own public address. Permit the host
# stub resolver solely on port 53. Existing networking is left in place.
build_uid=$(id -u backbond-build)
publish_uid=$(id -u backbond-publish)
image_build_uid=$(id -u backbond-image-build)
host_ipv4=$(ip -j -4 address show | python3 -c 'import ipaddress,json,sys; print(", ".join(sorted({a["local"] for i in json.load(sys.stdin) for a in i["addr_info"] if ipaddress.IPv4Address(a["local"]).is_global})))')
test -n "$host_ipv4"
test -r /etc/resolv.conf
grep -Eq '^nameserver 127\.0\.0\.53$' /etc/resolv.conf
if nft list table inet backbond_build_pool >/dev/null 2>&1; then
  echo 'Existing build firewall requires inspection; refusing to replace it.' >&2
  exit 1
fi
cat > /etc/backbond-build-pool/egress.nft <<NFT
table inet backbond_build_pool {
  chain output {
    type filter hook output priority -10; policy accept;
    meta skuid { $build_uid, $publish_uid, $image_build_uid } ip daddr 127.0.0.53 udp dport 53 accept
    meta skuid { $build_uid, $publish_uid, $image_build_uid } ip daddr 127.0.0.53 tcp dport 53 accept
    meta skuid { $build_uid, $publish_uid, $image_build_uid } meta nfproto ipv6 reject
    meta skuid { $build_uid, $publish_uid, $image_build_uid } ip daddr { $host_ipv4, 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.168.0.0/16, 224.0.0.0/4, 240.0.0.0/4 } reject
  }
}
NFT
nft --check --file /etc/backbond-build-pool/egress.nft
cat > /etc/systemd/system/backbond-build-egress.service <<'UNIT'
[Unit]
Description=Private-network denial for disposable build UIDs
Before=backbond-build-pool.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft --file /etc/backbond-build-pool/egress.nft
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/backbond-build-pool.service <<'UNIT'
[Unit]
Description=BackBond disposable Docker and publishing admission
Requires=backbond-build-egress.service
After=network-online.target backbond-build-egress.service
Wants=network-online.target
[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/backbond-build-pool/build_pool_broker.py
Restart=on-failure
RestartSec=30
TimeoutStopSec=120
UMask=0077
Environment=PYTHONDONTWRITEBYTECODE=1
StandardOutput=journal
StandardError=journal
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
echo 'Prepared, inactive. Supply restricted App credentials and pinned images, verify acceptance checks, then enable.'
