"""The narrow unit installer must install units and stay narrow.

BEAAA-30934: `install.sh` is the only path that puts a unit file under
/etc/systemd/system, so the drift PR #558 fixed could not be cleared without
also clearing and rebuilding `/opt/backbond/scan-venv`, rebuilding the Garak
eval venvs, rewriting `/etc/sudoers.d/backbond-scan-schedule` wholesale and
re-enabling both timers.  `install-units-only.sh` is the narrow counterpart:
it refreshes unit files, drops in one extra sudoers grant, and stops.

These tests pin both halves of that promise -- that it installs, and that it
does not grow into install.sh.  They point it at a temp directory so the
suite needs no privileges, the same arrangement
tests/test_beaaa30934_unit_drift_check.py uses.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

OPS_DIR = Path(__file__).resolve().parents[1] / "ops" / "scan-schedule"
HELPER = OPS_DIR / "install-units-only.sh"
WIDE_INSTALLER = OPS_DIR / "install.sh"
REPLAY_UNIT = "backbond-scan-vapi-beaaa30934.service"

UNIT_NAMES = sorted(
    p.name for p in OPS_DIR.iterdir() if p.suffix in {".service", ".timer"}
)

# Every behaviour the narrow installer exists to avoid, as it is spelled in
# install.sh.  test_narrowness_assertions_are_not_vacuous pins that these are
# real strings in the wide installer, so a rename cannot quietly turn the
# narrowness checks below into assertions about nothing.
WIDE_ONLY_BEHAVIOURS = (
    "venv --clear",
    "build_eval_framework_venvs.sh",
    "systemctl enable",
    "/etc/sudoers.d/backbond-scan-schedule",
)


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    unit_dir = tmp_path / "systemd"
    sudoers_dir = tmp_path / "sudoers"
    unit_dir.mkdir()
    sudoers_dir.mkdir()
    return unit_dir, sudoers_dir


def _run(unit_dir: Path, sudoers_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(HELPER), str(unit_dir), str(sudoers_dir)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_script_is_posix_parseable() -> None:
    """`sh -n` catches a bashism before root ever runs the thing."""
    proc = subprocess.run(
        ["sh", "-n", str(HELPER)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr


def test_install_leaves_no_drift(dirs: tuple[Path, Path]) -> None:
    """The whole point: after a run, the drift detector is clean."""
    unit_dir, sudoers_dir = dirs
    proc = _run(unit_dir, sudoers_dir)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.count("MATCH") == len(UNIT_NAMES)
    assert "ABSENT" not in proc.stdout
    assert "DRIFT" not in proc.stdout


def test_empty_target_starts_dirty(dirs: tuple[Path, Path]) -> None:
    """Negative control: the target really was empty before the install."""
    unit_dir, sudoers_dir = dirs
    before = subprocess.run(
        ["sh", str(OPS_DIR / "check-unit-drift.sh"), str(unit_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert before.returncode == 1
    assert before.stdout.count("ABSENT") == len(UNIT_NAMES)


def test_replay_unit_is_installed(dirs: tuple[Path, Path]) -> None:
    unit_dir, sudoers_dir = dirs
    assert _run(unit_dir, sudoers_dir).returncode == 0
    installed = unit_dir / REPLAY_UNIT
    assert installed.is_file()
    assert installed.read_text() == (OPS_DIR / REPLAY_UNIT).read_text()


def test_sudoers_dropin_is_narrow(dirs: tuple[Path, Path]) -> None:
    """Only start/status, only on the replay unit, only in its own file."""
    unit_dir, sudoers_dir = dirs
    assert _run(unit_dir, sudoers_dir).returncode == 0

    written = sorted(p.name for p in sudoers_dir.iterdir())
    assert written == ["backbond-scan-beaaa30934", "backbond-scan-beaaa39322"], written
    # install.sh owns backbond-scan-schedule; clobbering it would drop the
    # grants this file does not restate.
    assert not (sudoers_dir / "backbond-scan-schedule").exists()

    body = (sudoers_dir / "backbond-scan-beaaa30934").read_text().strip()
    assert body.count("\n") == 0, "expected exactly one grant line"
    assert body.startswith("beai-agent ALL=(root) NOPASSWD:")
    assert f"/usr/bin/systemctl start {REPLAY_UNIT}" in body
    assert f"/usr/bin/systemctl status {REPLAY_UNIT}" in body
    for verb in ("stop", "restart"):
        assert f"/usr/bin/systemctl {verb} " not in body
    # This drop-in remains scoped to the replay lane; the Zoo diagnostic has
    # its own independently testable drop-in.
    for name in UNIT_NAMES:
        if name != REPLAY_UNIT:
            assert name not in body


def _code(path: Path) -> str:
    """Executable lines only.

    The narrow installer's header comment names every behaviour it refuses,
    so a plain substring search over the whole file reports a side effect the
    script does not have.
    """
    return "\n".join(
        line
        for line in path.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


@pytest.mark.parametrize("behaviour", WIDE_ONLY_BEHAVIOURS)
def test_stays_narrow(behaviour: str) -> None:
    """None of install.sh's side effects may migrate into the narrow one."""
    assert behaviour not in _code(HELPER)


@pytest.mark.parametrize("behaviour", WIDE_ONLY_BEHAVIOURS)
def test_narrowness_assertions_are_not_vacuous(behaviour: str) -> None:
    """The strings above must still be what install.sh actually does."""
    assert behaviour in _code(WIDE_INSTALLER)


def test_code_stripper_keeps_real_lines() -> None:
    """A stripper that ate everything would make test_stays_narrow vacuous."""
    code = _code(HELPER)
    assert "systemctl daemon-reload" in code
    assert "check-unit-drift.sh" in code
    assert "BEAAA-30934: `install.sh` is the only path" not in code
