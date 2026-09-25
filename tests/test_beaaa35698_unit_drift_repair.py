"""Fast-forwarding the scan checkout must also make /etc match it.

BEAAA-35698: the nightly model-zoo scan was dead from 2026-09-12 to 2026-09-25
on ``PermissionError: [Errno 13] Permission denied:
/var/lib/backbond/scans/.worker-home`` -- a filesystem permission, not
OpenRouter credit.  It is the same failure BEAAA-30934 diagnosed on the weekly
Vapi lane: /etc/systemd/system held a pre-#558 unit, so ``HOME`` was unset and
``_ensure_worker_home()`` died EACCES on the 0750 parent.  The repair landed in
the repo as ``0dca4c9e4`` and never reached the box.

BEAAA-30934 built the detector (``check-unit-drift.sh``) and the repair
(``install-units-only.sh``) and deliberately wired neither into any unit --
``tests/test_scheduled_scan_beaaa30240.py`` pins that the scan units invoke no
helper of this kind.  So the drift went unnoticed for thirteen days because
nothing ran the detector.  ``backbond-scan-schedule-update`` is where it now
runs: an operator-invoked script, on the other side of the merge that creates
the drift.

These tests own that script's logic.  The real detector runs for real against a
real temp unit dir; the repair step is shimmed in the repair case so the test
needs no privileges, because ``install-units-only.sh`` is already covered end to
end by tests/test_beaaa30934_install_units_only.py.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEDULE_DIR = ROOT / "ops" / "scan-schedule"
SCRIPT_NAME = "backbond-scan-schedule-update"
UNITS = sorted(
    p.name for p in SCHEDULE_DIR.iterdir() if p.suffix in {".service", ".timer"}
)


@pytest.fixture()
def host(tmp_path: Path):
    """A fake checkout plus unit dir, and a callable that runs the script.

    ``git`` is shimmed: the fetch/merge half is not what these tests own, and a
    real remote would make them network-dependent.
    """

    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    (shim_dir / "git").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (shim_dir / "git").chmod(0o755)

    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    shutil.copytree(SCHEDULE_DIR, checkout / "ops" / "scan-schedule")
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()

    def run(*, as_root: bool = False) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PATH"] = f"{shim_dir}:{env['PATH']}"
        if as_root:
            (shim_dir / "id").write_text("#!/bin/sh\necho 0\n", encoding="utf-8")
            (shim_dir / "id").chmod(0o755)
        return subprocess.run(
            ["sh", str(SCHEDULE_DIR / SCRIPT_NAME), str(checkout), str(unit_dir)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    run.checkout = checkout  # type: ignore[attr-defined]
    run.unit_dir = unit_dir  # type: ignore[attr-defined]
    run.shim_dir = shim_dir  # type: ignore[attr-defined]
    return run


def _install_units(unit_dir: Path) -> None:
    for name in UNITS:
        shutil.copyfile(SCHEDULE_DIR / name, unit_dir / name)


def test_matching_units_are_reported_and_nothing_is_repaired(host):
    _install_units(host.unit_dir)
    proc = host()
    assert proc.returncode == 0, proc.stderr
    assert "nothing to repair" in proc.stdout


def test_absent_units_are_reported_as_drift(host):
    proc = host()
    assert proc.returncode == 1
    assert "drifted" in proc.stderr
    assert "install-units-only.sh" in proc.stderr


def test_a_stale_installed_unit_is_detected(host):
    """The BEAAA-35698 failure itself: /etc held a unit older than the repo."""

    _install_units(host.unit_dir)
    stale = host.unit_dir / "backbond-scan-zoo.service"
    stale.write_text(
        (SCHEDULE_DIR / "backbond-scan-zoo.service")
        .read_text(encoding="utf-8")
        .replace("Environment=HOME=/var/lib/backbond/scan-home\n", ""),
        encoding="utf-8",
    )
    proc = host()
    assert proc.returncode == 1
    assert "DRIFT" in proc.stdout
    assert "backbond-scan-zoo.service" in proc.stdout


def test_root_repairs_the_drift_and_proves_it(host):
    """As root the script repairs, then re-checks, and reports what it left."""

    installer = host.checkout / "ops" / "scan-schedule" / "install-units-only.sh"
    log = host.shim_dir / "installer.log"
    installer.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        f'for u in {" ".join(UNITS)}; do cp "{SCHEDULE_DIR}/$u" "$1/$u"; done\n',
        encoding="utf-8",
    )
    installer.chmod(0o755)

    proc = host(as_root=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert log.read_text(encoding="utf-8").strip() == str(host.unit_dir)
    assert "Units repaired" in proc.stdout
    # The re-check is the proof: every unit reports MATCH after the repair.
    assert proc.stdout.count("MATCH") >= len(UNITS)


def test_no_timer_is_ever_enabled_or_started():
    """BEAAA-30240: both timers carry Persistent=true.

    `--now` -- and a bare `start` on a timer whose stamp is missing -- fires an
    immediate catch-up scan on a host that is still being provisioned, which
    would spend OpenRouter credit this issue is explicitly not authorised to
    spend.  A deliberately disabled timer must also survive this script.
    """

    text = (SCHEDULE_DIR / SCRIPT_NAME).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    assert "systemctl" not in code
    assert "--now" not in code


def test_the_script_uses_the_checkout_it_just_fast_forwarded():
    """Repairing from the repo's own copy would defeat the point.

    The units that must reach /etc are the ones the merge just brought in, so
    both helpers are invoked out of ``$checkout``, not out of wherever this
    script happens to sit.
    """

    code = (SCHEDULE_DIR / SCRIPT_NAME).read_text(encoding="utf-8")
    assert 'schedule_dir="$checkout/ops/scan-schedule"' in code
    assert '"$schedule_dir/check-unit-drift.sh"' in code
    assert '"$schedule_dir/install-units-only.sh"' in code
