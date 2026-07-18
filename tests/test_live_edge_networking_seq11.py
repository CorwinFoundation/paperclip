from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "infra" / "live-edge" / "backbond-ensure-docker-networking"
NETWORK_UNIT = ROOT / "infra" / "live-edge" / "backbond-docker-networking.service"
LIVE_UNIT = ROOT / "infra" / "live-edge" / "backbond-scanner-live.service"
DEPLOY_UNIT = ROOT / "infra" / "live-edge" / "backbond-deploy-agent.service"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def test_network_repair_is_idempotent_and_scoped(tmp_path: Path) -> None:
    if os.name != "posix" or os.geteuid() != 0:
        return
    state = tmp_path / "rules"
    iptables = tmp_path / "iptables"
    ip = tmp_path / "ip"
    sysctl = tmp_path / "sysctl"
    _write_executable(
        iptables,
        f'''#!/usr/bin/env bash
set -euo pipefail
state={state!s}
args="$*"
if [[ "$args" == *" -S DOCKER"* ]]; then
  probe="${{args/ -S / -N }}"
  grep -Fx -- "$probe" "$state" >/dev/null 2>&1
  exit $?
fi
if [[ "$args" == *" -C "* ]]; then
  probe="${{args/ -C / -A }}"
  grep -Fx -- "$probe" "$state" >/dev/null 2>&1
  exit $?
fi
if [[ "$args" == *" -N DOCKER"* || "$args" == *" -A "* ]]; then
  printf '%s\\n' "$args" >> "$state"
fi
''',
    )
    _write_executable(ip, "#!/usr/bin/env bash\n[[ \"$*\" == \"link show docker0\" ]]\n")
    _write_executable(sysctl, "#!/usr/bin/env bash\n[[ \"$*\" == \"-q -w net.ipv4.conf.docker0.route_localnet=1\" ]]\n")
    state.write_text("", encoding="utf-8")
    env = {**os.environ, "IPTABLES": str(iptables), "IP": str(ip), "SYSCTL": str(sysctl)}

    first = subprocess.run(["bash", str(SCRIPT)], env=env, text=True, capture_output=True)
    assert first.returncode == 0, first.stderr
    first_rules = state.read_text(encoding="utf-8").splitlines()
    second = subprocess.run(["bash", str(SCRIPT)], env=env, text=True, capture_output=True)
    assert second.returncode == 0, second.stderr
    assert state.read_text(encoding="utf-8").splitlines() == first_rules
    assert any("-s 127.0.0.0/8 -d 172.17.0.0/16 -j MASQUERADE" in rule for rule in first_rules)
    assert all("0.0.0.0/0" not in rule for rule in first_rules)


def test_units_require_host_networking_and_container_disables_direct_nft() -> None:
    network_unit = NETWORK_UNIT.read_text(encoding="utf-8")
    live_unit = LIVE_UNIT.read_text(encoding="utf-8")
    deploy_unit = DEPLOY_UNIT.read_text(encoding="utf-8")

    assert "ExecStart=/usr/local/sbin/backbond-ensure-docker-networking" in network_unit
    assert "Before=backbond-scanner-live.service backbond-deploy-agent.service" in network_unit
    for unit in (live_unit, deploy_unit):
        assert "Requires=backbond-docker-networking.service" in unit
        assert "After=" in unit and "backbond-docker-networking.service" in unit
        assert "ExecStartPre=/usr/local/sbin/backbond-ensure-docker-networking" in unit
    assert "--env ARE_DIRECT_EGRESS_NFT=0" in live_unit
