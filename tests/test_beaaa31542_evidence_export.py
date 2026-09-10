"""BEAAA-31542: the retained-evidence export is a scanner-deploy subcommand.

The deploy seat on srv1749248 holds one NOPASSWD entry, /usr/local/sbin/scanner-deploy,
and cannot read /var/lib/are-scanner (mode 0710) or reach the docker socket. These
tests pin the export path that runs under that single grant.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[1] / "scripts" / "deploy" / "scanner-deploy.sh"


def _run(state_dir: Path, export_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["SCANNER_EVIDENCE_STATE_DIR"] = str(state_dir)
    env["SCANNER_EVIDENCE_EXPORT_DIR"] = str(export_dir)
    env.pop("SUDO_UID", None)
    env.pop("SUDO_GID", None)
    return subprocess.run(
        ["bash", str(HELPER), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture()
def populated_state(tmp_path: Path) -> Path:
    state = tmp_path / "state"
    retained = state / "retained-evidence"
    retained.mkdir(parents=True)
    (retained / "production-like-target-output.json").write_text(
        json.dumps({"rows": [{"row_id": n} for n in range(187)]}), encoding="utf-8"
    )
    (retained / "nested").mkdir()
    (retained / "nested" / "transcript.jsonl").write_text('{"turn": 1}\n', encoding="utf-8")
    return state


def test_export_writes_archive_and_manifest(populated_state: Path, tmp_path: Path) -> None:
    export_dir = tmp_path / "exports"
    result = _run(populated_state, export_dir, "export-evidence")
    assert result.returncode == 0, result.stderr

    archive_path, manifest_path = (Path(line) for line in result.stdout.split())
    assert archive_path.parent == export_dir
    assert archive_path.exists() and manifest_path.exists()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "backbond-retained-evidence-export@1"
    assert manifest["file_count"] == 2
    assert manifest["archive_name"] == archive_path.name
    assert manifest["archive_sha256"] == hashlib.sha256(archive_path.read_bytes()).hexdigest()

    by_path = {entry["path"]: entry for entry in manifest["entries"]}
    source = populated_state / "retained-evidence" / "production-like-target-output.json"
    assert by_path["production-like-target-output.json"]["sha256"] == hashlib.sha256(
        source.read_bytes()
    ).hexdigest()
    assert "nested/transcript.jsonl" in by_path

    with tarfile.open(archive_path) as tar:
        names = set(tar.getnames())
    assert "retained-evidence/production-like-target-output.json" in names
    assert "retained-evidence/nested/transcript.jsonl" in names


def test_export_creates_its_own_export_directory(populated_state: Path, tmp_path: Path) -> None:
    export_dir = tmp_path / "missing" / "exports"
    assert not export_dir.exists()
    assert _run(populated_state, export_dir, "export-evidence").returncode == 0
    assert export_dir.is_dir()


def test_export_rejects_extra_arguments(populated_state: Path, tmp_path: Path) -> None:
    result = _run(populated_state, tmp_path / "exports", "export-evidence", "/etc/shadow")
    assert result.returncode != 0
    assert "usage" in result.stderr


def test_export_fails_closed_when_evidence_is_absent(tmp_path: Path) -> None:
    result = _run(tmp_path / "empty", tmp_path / "exports", "export-evidence")
    assert result.returncode != 0
    assert "retained-evidence directory missing" in result.stderr


def test_export_refuses_a_symlinked_evidence_directory(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (tmp_path / "elsewhere").mkdir()
    (state / "retained-evidence").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    result = _run(state, tmp_path / "exports", "export-evidence")
    assert result.returncode != 0
    assert "symlink" in result.stderr


def test_activation_usage_is_unchanged(tmp_path: Path) -> None:
    """The five-argument activation form the push lane calls must still be the default."""
    result = _run(tmp_path / "state", tmp_path / "exports", "deadbeef")
    assert result.returncode != 0
    assert "usage: scanner-deploy <commit-sha>" in result.stderr
