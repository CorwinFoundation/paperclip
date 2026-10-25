#!/usr/bin/env bash
# Run only after reviewed broker integration and isolation acceptance.
set -euo pipefail
test "$(id -u)" = 0
test "$(id -u backbond-ci-control)" = 1006
account_home=/var/lib/backbond-ci-control
run_control() {
  (cd "$account_home" && runuser -u backbond-ci-control -- env HOME="$account_home" \
    XDG_RUNTIME_DIR=/run/user/1006 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1006/bus \
    systemd-run --user --quiet --wait --collect --pipe --service-type=exec \
    --property=KillMode=process \
    --property=OOMScoreAdjust=1000 --working-directory="$account_home" \
    --setenv=HOME="$account_home" --setenv=XDG_RUNTIME_DIR=/run/user/1006 \
    --setenv=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1006/bus -- "$@")
}
systemctl is-active --quiet backbond-ci-control-egress.service
nft list table inet backbond_ci_control >/dev/null
run_control podman inspect --format '{{.State.Running}}' backbond-ci-control | grep -qx true
run_control podman exec backbond-ci-control python3 -c \
  'import json,pathlib; d=json.loads(pathlib.Path(".runner").read_text(encoding="utf-8-sig")); assert d["agentName"] == "backbond-ci-control-srv1799443"; assert d["gitHubUrl"].rstrip("/") == "https://github.com/CorwinFoundation/are-scanner"; assert d["workFolder"] == "_work"'
run_control podman exec -i backbond-ci-control python3 - --check < /etc/backbond-ci-control/control_worker_probe.py
service_dir=$account_home/.config/systemd/user
test ! -e "$service_dir/backbond-ci-control-runner.service"
install -d -m 700 -o backbond-ci-control -g backbond-ci-control "$service_dir"
cat > "$service_dir/backbond-ci-control-runner.service" <<'UNIT'
[Unit]
Description=BackBond memory-limited CI control runner
[Service]
ExecStartPre=/usr/bin/podman start backbond-ci-control
ExecStart=/usr/bin/podman exec backbond-ci-control ./run.sh
ExecStop=-/usr/bin/podman exec backbond-ci-control /bin/bash -c 'pkill --signal INT --exact Runner.Listener; for attempt in {1..30}; do pgrep --exact Runner.Listener >/dev/null || exit 0; sleep 1; done'
ExecStop=/usr/bin/podman stop --time 10 backbond-ci-control
OOMScoreAdjust=1000
Restart=on-failure
RestartSec=30
TimeoutStopSec=50
[Install]
WantedBy=default.target
UNIT
chown backbond-ci-control:backbond-ci-control "$service_dir/backbond-ci-control-runner.service"
chmod 600 "$service_dir/backbond-ci-control-runner.service"
run_control systemctl --user daemon-reload
run_control systemctl --user enable --now backbond-ci-control-runner.service
run_control systemctl --user --no-pager status backbond-ci-control-runner.service
