"""Installed scan units drifting from the checkout must be detectable.

BEAAA-30934: the three scan services gained `Environment=HOME=` and a
root-prefixed self-heal `ExecStartPre` in PR #558, but a unit file only
reaches /etc/systemd/system through `install.sh`, which needs root.  The
scan units fast-forward the checkout on every start and never reinstall
themselves, so the box ran the pre-#558 copy while both the repo and the
deployed tree already carried the fix -- and the weekly Vapi scan exited 1
on `PermissionError: /var/lib/backbond/scans/.worker-home` before
dispatching a probe.  Nothing reported the staleness.

These tests pin the detector.  They point it at a temp directory rather
than /etc/systemd/system so the suite neither needs privileges nor depends
on the state of the host it runs on.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

OPS_DIR = Path(__file__).resolve().parents[1] / "ops" / "scan-schedule"
HELPER = OPS_DIR / "check-unit-drift.sh"

UNIT_NAMES = sorted(
    p.name for p in OPS_DIR.iterdir() if p.suffix in {".service", ".timer"}
)


def _run(unit_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(HELPER), str(unit_dir)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def installed(tmp_path: Path) -> Path:
    """A unit directory that is a faithful copy of the checkout's units."""
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    for name in UNIT_NAMES:
        shutil.copyfile(OPS_DIR / name, unit_dir / name)
    return unit_dir


def test_there_are_units_to_compare() -> None:
    """A silently empty glob would make every other assertion vacuous."""
    assert UNIT_NAMES, "no .service/.timer files in ops/scan-schedule"
    assert "backbond-scan-vapi.service" in UNIT_NAMES


def test_faithful_copy_is_clean(installed: Path) -> None:
    proc = _run(installed)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.count("MATCH") == len(UNIT_NAMES)
    assert "DRIFT" not in proc.stdout
    assert "ABSENT" not in proc.stdout


def test_a_changed_unit_is_reported_as_drift(installed: Path) -> None:
    """The real defect: content differs, filename and mtime look fine."""
    target = installed / "backbond-scan-vapi.service"
    text = target.read_text(encoding="utf-8")
    stale = "\n".join(
        line for line in text.splitlines() if not line.startswith("Environment=HOME=")
    )
    assert stale != text, "fixture no longer removes anything"
    target.write_text(stale + "\n", encoding="utf-8")

    proc = _run(installed)
    assert proc.returncode == 1
    assert "DRIFT  backbond-scan-vapi.service" in proc.stdout
    assert "stale against this checkout" in proc.stderr


def test_the_exact_pre_558_regression_is_caught(installed: Path) -> None:
    """Drop both lines PR #558 added; that is the state the host was in."""
    target = installed / "backbond-scan-vapi.service"
    kept = [
        line
        for line in target.read_text(encoding="utf-8").splitlines()
        if not line.startswith("Environment=HOME=")
        and "chmod o+x /var/lib/backbond" not in line
    ]
    target.write_text("\n".join(kept) + "\n", encoding="utf-8")

    proc = _run(installed)
    assert proc.returncode == 1
    assert "DRIFT  backbond-scan-vapi.service" in proc.stdout


def test_a_missing_unit_is_reported_as_absent(installed: Path) -> None:
    (installed / "backbond-scan-vapi-beaaa30934.service").unlink()
    proc = _run(installed)
    assert proc.returncode == 1
    assert "ABSENT backbond-scan-vapi-beaaa30934.service" in proc.stdout


def test_an_empty_unit_dir_is_all_absent(tmp_path: Path) -> None:
    empty = tmp_path / "none"
    empty.mkdir()
    proc = _run(empty)
    assert proc.returncode == 1
    assert proc.stdout.count("ABSENT") == len(UNIT_NAMES)


def test_extra_files_in_unit_dir_are_ignored(installed: Path) -> None:
    """Only units this checkout owns are compared, not the whole directory."""
    (installed / "unrelated.service").write_text("[Unit]\n", encoding="utf-8")
    proc = _run(installed)
    assert proc.returncode == 0, proc.stderr
    assert "unrelated.service" not in proc.stdout


def test_the_units_still_carry_the_558_fix() -> None:
    """Guard the fix itself: losing either line reintroduces the outage."""
    for name in (
        "backbond-scan-vapi.service",
        "backbond-scan-zoo.service",
        "backbond-scan-vapi-garak.service",
    ):
        text = (OPS_DIR / name).read_text(encoding="utf-8")
        assert "Environment=HOME=/var/lib/backbond/scan-home" in text, name
        # The `+` prefix is load-bearing: the chmod must run as root, not as
        # the unprivileged unit user.
        assert "ExecStartPre=+/bin/sh -c" in text, name
        assert "chmod o+x /var/lib/backbond" in text, name
