"""The full installer must discover unit files the same way its siblings do.

BEAAA-35791: `ops/scan-schedule/install.sh` discovered units from a hardcoded
six-name loop while `install-units-only.sh` and `check-unit-drift.sh` both
globbed `*.service` / `*.timer`.  All three agreed only because the directory
held exactly those six files.  Add a seventh and the detector reported drift,
the narrow installer repaired it, and the full installer -- the one path that
stands up a fresh host -- silently skipped it and still printed its success
line and exited 0.  That is the same failure class BEAAA-30934 and
BEAAA-35698 chased: a unit change that lands in the repo and never reaches
/etc/systemd/system.

install.sh now delegates unit installation to install-units-only.sh, so there
is one discovery loop in the directory rather than three.  These tests pin
that: the canary test proves a new unit reaches the installed set through the
path install.sh uses, and the source tests pin that install.sh can no longer
select units by name.

install.sh itself is not executed here -- it requires root, clears
/opt/backbond/scan-venv and rebuilds the Garak eval venvs.  The delegated
helper takes a unit_dir argument for exactly this reason, the same
arrangement tests/test_beaaa30934_install_units_only.py and
tests/test_beaaa30934_unit_drift_check.py use.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

OPS_DIR = Path(__file__).resolve().parents[1] / "ops" / "scan-schedule"
INSTALLER = OPS_DIR / "install.sh"
HELPER_NAME = "install-units-only.sh"
DRIFT_NAME = "check-unit-drift.sh"
CANARY = "backbond-scan-beaaa35791-canary.service"

# The exact loop this ticket removed, so the source assertions below cannot
# quietly become assertions about nothing.
PRE_FIX_LOOP = (
    "for unit in backbond-scan-vapi.service backbond-scan-vapi-beaaa30934.service"
    ' backbond-scan-vapi-garak.service backbond-scan-vapi.timer'
    ' backbond-scan-zoo.service backbond-scan-zoo.timer; do\n'
    '  install -o root -g root -m 0644 "$here/$unit" "/etc/systemd/system/$unit"\n'
    "done\n"
)

UNIT_SUFFIXES = {".service", ".timer"}


def _code(path: Path) -> str:
    """The script with comment-only lines dropped.

    The rationale comments name the units and the removed loop, so a check
    over the raw text would pass or fail on prose rather than on code.
    """
    kept = [
        line
        for line in path.read_text().splitlines()
        if not line.lstrip().startswith("#")
    ]
    return "\n".join(kept) + "\n"


def _unit_install_lines(code: str) -> list[str]:
    """Lines that write a file into a systemd unit directory."""
    return [
        line
        for line in code.splitlines()
        if "systemd/system" in line
        and re.search(r"\binstall\b", line)
        and not re.search(r"\binstall\s+-d\b", line)  # -d creates the dir
    ]


def test_installer_is_posix_parseable() -> None:
    assert subprocess.run(["sh", "-n", str(INSTALLER)]).returncode == 0


def test_a_new_unit_file_reaches_the_installed_set(tmp_path: Path) -> None:
    """The ticket's failure scenario: add a seventh unit, change nothing else."""
    src_dir = tmp_path / "scan-schedule"
    shutil.copytree(OPS_DIR, src_dir)
    canary = src_dir / CANARY
    canary.write_text(
        "[Unit]\nDescription=BEAAA-35791 discovery canary\n"
        "[Service]\nType=oneshot\nExecStart=/bin/true\n"
    )
    unit_dir = tmp_path / "systemd"
    sudoers_dir = tmp_path / "sudoers"
    unit_dir.mkdir()
    sudoers_dir.mkdir()

    run = subprocess.run(
        ["sh", str(src_dir / HELPER_NAME), str(unit_dir), str(sudoers_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr

    installed = unit_dir / CANARY
    assert installed.is_file(), f"{CANARY} was not installed: {run.stdout}"
    assert installed.read_bytes() == canary.read_bytes()
    # Every other unit came along too -- the canary did not displace them.
    expected = {p.name for p in src_dir.iterdir() if p.suffix in UNIT_SUFFIXES}
    assert {p.name for p in unit_dir.iterdir()} == expected


def test_installer_delegates_unit_installation() -> None:
    code = _code(INSTALLER)
    assert HELPER_NAME in code, (
        "install.sh must delegate unit installation to the glob-driven helper"
    )
    assert not _unit_install_lines(code), (
        "install.sh installs a unit file itself again: "
        f"{_unit_install_lines(code)}"
    )


def test_installer_selects_no_unit_by_name() -> None:
    """Unit *installation* must not name a file; enablement still may."""
    for line in _code(INSTALLER).splitlines():
        if "systemctl" in line or "sudoers" in line or "NOPASSWD" in line:
            continue  # enabling a timer and granting sudo are per-unit decisions
        named = [
            word
            for word in re.findall(r"[\w.-]+", line)
            if Path(word).suffix in UNIT_SUFFIXES
        ]
        assert not named, f"install.sh names unit file(s) {named} in: {line}"


def test_installer_proves_its_own_effect() -> None:
    code = _code(INSTALLER)
    assert DRIFT_NAME in code, (
        "install.sh must end by running the drift detector rather than "
        "asserting success in an echo"
    )
    lines = [line for line in code.splitlines() if line.strip()]
    drift_at = max(i for i, line in enumerate(lines) if DRIFT_NAME in line)
    enable_at = max(i for i, line in enumerate(lines) if "systemctl enable" in line)
    assert drift_at > enable_at, (
        "the drift check must run after the timers are enabled, so the "
        "success line is earned by the whole run"
    )


def test_source_assertions_are_not_vacuous() -> None:
    """The checks above must fire on the loop this ticket removed."""
    assert _unit_install_lines(PRE_FIX_LOOP), (
        "the unit-install detector no longer recognises the pre-fix loop"
    )
    named = [
        word
        for word in re.findall(r"[\w.-]+", PRE_FIX_LOOP)
        if Path(word).suffix in UNIT_SUFFIXES
    ]
    assert len(named) >= 6, "the by-name detector no longer recognises the pre-fix loop"


def test_comment_stripper_keeps_real_lines() -> None:
    code = _code(INSTALLER)
    assert "set -eu" in code
    assert "systemctl enable backbond-scan-vapi.timer" in code
    assert "BEAAA-35791" not in code, "comment-only lines should have been dropped"
