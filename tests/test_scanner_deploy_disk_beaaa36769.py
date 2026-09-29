"""Focused tests for BEAAA-36769 bounded disk maintenance."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/deploy/scanner-deploy.sh"


def _exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def _env(tmp_path: Path) -> tuple[dict[str, str], Path, Path, Path]:
    archive = tmp_path / "archives"
    archive.mkdir()
    stale = archive / ".scanner-deploy.123.tmp"
    stale.write_text("stale")
    os.utime(stale, (1, 1))
    protected = tmp_path / "backbond-stage-b-dbcee248-recovered-20260929.tar"
    protected.write_text("keep")
    calls = tmp_path / "docker.calls"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "docker", f'''#!/bin/sh
echo "$*" >>"{calls}"
case "$1 $2" in
  "system df") echo "TYPE TOTAL ACTIVE SIZE RECLAIMABLE";;
  "image ls") echo sha256:dangling-old;;
  "image inspect") echo 2000-01-01T00:00:00Z;;
  "image rm") exit 0;;
esac
''')
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "SCANNER_DOCKER": str(bin_dir / "docker"),
        "SCANNER_ARCHIVE_DIR": str(archive),
        "SCANNER_DISK_PATH": str(tmp_path),
        "SCANNER_DISK_MIN_FREE_BYTES": "0",
        "SCANNER_DISK_MIN_FREE_INODE_PERCENT": "0",
        "SCANNER_DISK_RETENTION_DAYS": "0",
        "SCANNER_PROTECTED_RECOVERY_ARCHIVE": str(protected),
    }
    return env, stale, protected, calls


def test_dry_run_reports_candidates_and_changes_nothing(tmp_path: Path) -> None:
    env, stale, protected, calls = _env(tmp_path)
    result = subprocess.run(
        ["bash", str(HELPER), "disk-maintenance"], env=env, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
    assert stale.exists() and protected.exists()
    assert "dry-run temporary:" in result.stderr
    assert "dry-run dangling-image: sha256:dangling-old" in result.stderr
    assert "image rm" not in calls.read_text()


def test_apply_is_idempotent_and_never_selects_protected_assets(tmp_path: Path) -> None:
    env, stale, protected, calls = _env(tmp_path)
    first = subprocess.run(
        ["bash", str(HELPER), "disk-maintenance", "--apply"], env=env, text=True, capture_output=True
    )
    second = subprocess.run(
        ["bash", str(HELPER), "disk-maintenance", "--apply"], env=env, text=True, capture_output=True
    )
    assert first.returncode == second.returncode == 0
    assert not stale.exists() and protected.read_text() == "keep"
    assert str(protected) not in first.stderr + second.stderr
    assert calls.read_text().count("image rm sha256:dangling-old") == 2


def test_threshold_failure_is_nonzero(tmp_path: Path) -> None:
    env, _, protected, _ = _env(tmp_path)
    env["SCANNER_DISK_MIN_FREE_BYTES"] = str(10**30)
    result = subprocess.run(
        ["bash", str(HELPER), "disk-maintenance"], env=env, text=True, capture_output=True
    )
    assert result.returncode != 0
    assert "free bytes below minimum" in result.stderr
    assert protected.exists()
