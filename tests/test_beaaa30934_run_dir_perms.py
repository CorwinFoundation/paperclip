"""The single root install run must leave the 2026-09-08 evidence readable.

BEAAA-30934: `install.sh` chowns ``/var/lib/backbond/scans`` but not its
contents, so a run directory written by another uid stays unreadable after the
one Founder command.  That would cost a second root step.  These tests pin the
helper that closes it, including the thing it must NOT touch.

``chown`` needs privileges these tests do not have, so it is shimmed on PATH
and its argv recorded.  The mode changes are real.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[1] / "ops" / "scan-schedule" / "relax-run-dir-perms.sh"


def _run(manifest_root: Path, tmp_path: Path, unit_user: str = "backbond-scan"):
    """Invoke the helper with ``chown`` shimmed, returning its recorded argv."""

    shim_dir = tmp_path / "shim"
    shim_dir.mkdir(exist_ok=True)
    log = tmp_path / "chown.log"
    (shim_dir / "chown").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CHOWN_LOG"\n', encoding="utf-8"
    )
    (shim_dir / "chown").chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{shim_dir}:{env['PATH']}"
    env["CHOWN_LOG"] = str(log)
    proc = subprocess.run(
        ["sh", str(HELPER), str(manifest_root), unit_user],
        capture_output=True, text=True, env=env, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    """A scans root shaped like AriClaw's: a private run dir and a HOME dir."""

    root = tmp_path / "scans"
    evidence = root / "2026-09-08" / "vapi" / "evidence" / "wave0.out_of_scope"
    evidence.mkdir(parents=True)
    (evidence / "case.json").write_text('{"verdict": "fail"}', encoding="utf-8")
    # The pre-install state: readable only by the writing uid.
    for path in (root / "2026-09-08", evidence.parent.parent, evidence.parent, evidence):
        path.chmod(0o700)
    (evidence / "case.json").chmod(0o600)
    worker_home = root / ".worker-home"
    (worker_home / ".config").mkdir(parents=True)
    (worker_home / ".config" / "credentials").write_text("token", encoding="utf-8")
    worker_home.chmod(0o700)
    (worker_home / ".config" / "credentials").chmod(0o600)
    return root


def test_run_directory_becomes_traversable_and_readable(store: Path) -> None:
    """The failure this fixes: evidence unreadable after the one root run."""

    evidence = store / "2026-09-08" / "vapi" / "evidence"
    assert not _mode(evidence) & 0o005  # precondition: unreadable by others
    _run(store, store.parent)
    for path in (store / "2026-09-08", evidence, evidence / "wave0.out_of_scope"):
        assert _mode(path) & 0o005 == 0o005, f"{path} not traversable+readable"
    assert _mode(evidence / "wave0.out_of_scope" / "case.json") & 0o004


def test_worker_home_is_not_exposed(store: Path) -> None:
    """The dotfile glob is load-bearing: .worker-home is a HOME directory."""

    home = store / ".worker-home"
    _run(store, store.parent)
    assert _mode(home) == 0o700, "worker home must not become world-readable"
    assert _mode(home / ".config" / "credentials") == 0o600


def test_files_do_not_gain_the_execute_bit(store: Path) -> None:
    """``a+rX`` is capital-X on purpose: directories only."""

    _run(store, store.parent)
    case = store / "2026-09-08" / "vapi" / "evidence" / "wave0.out_of_scope" / "case.json"
    assert not _mode(case) & 0o111


def test_run_directories_are_chowned_to_the_unit_user(store: Path) -> None:
    calls = _run(store, store.parent)
    assert any("-R backbond-scan:backbond-scan" in call and "2026-09-08" in call
               for call in calls), calls
    assert not any(".worker-home" in call for call in calls), calls


def test_empty_store_is_not_an_error(tmp_path: Path) -> None:
    """An unglobbed ``*`` must not be chowned literally."""

    root = tmp_path / "scans"
    root.mkdir()
    assert _run(root, tmp_path) == []


def test_missing_root_fails_loudly(tmp_path: Path) -> None:
    proc = subprocess.run(
        ["sh", str(HELPER), str(tmp_path / "absent"), "backbond-scan"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 1
    assert "not a directory" in proc.stderr
