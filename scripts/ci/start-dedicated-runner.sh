#!/usr/bin/env bash
# Run as root after registration and isolation checks have passed.
set -euo pipefail
test "$(id -u)" = 0
runner_root=/var/lib/backbond-ci
runner_uid=$(id -u backbond-ci)
run_ci() {
  (cd "$runner_root" && runuser -u backbond-ci -- env HOME="$runner_root" \
    XDG_RUNTIME_DIR="/run/user/$runner_uid" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$runner_uid/bus" "$@")
}
run_ci podman exec backbond-ci test -s /home/backbond-ci/runner/.runner
service_dir=$runner_root/.config/systemd/user
test ! -e "$service_dir/backbond-ci-runner.service"
install -d -m 700 -o backbond-ci -g backbond-ci "$service_dir"
cat > "$service_dir/backbond-ci-runner.service" <<'UNIT'
[Unit]
Description=BackBond isolated test runner

[Service]
ExecStartPre=/usr/bin/podman start backbond-ci
ExecStart=/usr/bin/podman exec backbond-ci ./run.sh
ExecStop=-/usr/bin/podman exec backbond-ci /bin/bash -c 'pkill --signal INT --exact Runner.Listener; for attempt in {1..30}; do pgrep --exact Runner.Listener >/dev/null || exit 0; sleep 1; done'
ExecStop=/usr/bin/podman stop --time 10 backbond-ci
Restart=always
RestartSec=10
TimeoutStopSec=50

[Install]
WantedBy=default.target
UNIT
chown backbond-ci:backbond-ci "$service_dir/backbond-ci-runner.service"
chmod 600 "$service_dir/backbond-ci-runner.service"
run_ci systemctl --user daemon-reload
run_ci systemctl --user enable --now backbond-ci-runner.service
run_ci systemctl --user --no-pager status backbond-ci-runner.service
