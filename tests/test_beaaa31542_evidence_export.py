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


JOB_ID = "a935915a-ddc0-473f-ba46-ebd3593cc38b"


@pytest.fixture()
def job_state(tmp_path: Path) -> Path:
    """BEAAA-31116: the rank-2 roster resolves to evidence/<job-id>, not retained-evidence."""
    state = tmp_path / "state"
    job = state / "evidence" / JOB_ID
    (job / "tool_misuse_scope_escape").mkdir(parents=True)
    (job / "manifest.json").write_text('{"job_id": "%s"}' % JOB_ID, encoding="utf-8")
    (job / "tool_misuse_scope_escape" / "TOOL_MISUSE_SE-01.json").write_text(
        json.dumps({"recorded_target_response": "hello", "verdict": "fail"}), encoding="utf-8"
    )
    (state / "evidence" / "other-job").mkdir()
    (state / "evidence" / "other-job" / "leak.json").write_text("{}", encoding="utf-8")
    return state


def test_job_export_archives_only_the_requested_job(job_state: Path, tmp_path: Path) -> None:
    export_dir = tmp_path / "exports"
    result = _run(job_state, export_dir, "export-evidence", "job", JOB_ID)
    assert result.returncode == 0, result.stderr

    archive_path, manifest_path = (Path(line) for line in result.stdout.split())
    assert archive_path.name.startswith(f"job-evidence-{JOB_ID}-")

    with tarfile.open(archive_path) as tar:
        names = set(tar.getnames())
    case = f"evidence/{JOB_ID}/tool_misuse_scope_escape/TOOL_MISUSE_SE-01.json"
    assert case in names
    assert f"evidence/{JOB_ID}/manifest.json" in names
    assert not any(name.startswith("evidence/other-job") for name in names)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["file_count"] == 2
    by_path = {entry["path"]: entry for entry in manifest["entries"]}
    source = job_state / "evidence" / JOB_ID / "tool_misuse_scope_escape" / "TOOL_MISUSE_SE-01.json"
    assert by_path["tool_misuse_scope_escape/TOOL_MISUSE_SE-01.json"]["sha256"] == hashlib.sha256(
        source.read_bytes()
    ).hexdigest()


def test_job_export_rejects_a_job_id_that_is_not_a_uuid(job_state: Path, tmp_path: Path) -> None:
    for bad in ("../../etc", "A935915A-DDC0-473F-BA46-EBD3593CC38B", f"{JOB_ID}/x", ""):
        result = _run(job_state, tmp_path / "exports", "export-evidence", "job", bad)
        assert result.returncode != 0, bad
        assert "job id must be a lowercase uuid" in result.stderr, bad


def test_job_export_requires_exactly_one_job_id(job_state: Path, tmp_path: Path) -> None:
    result = _run(job_state, tmp_path / "exports", "export-evidence", "job")
    assert result.returncode != 0
    assert "usage: scanner-deploy export-evidence job <job-id>" in result.stderr


def test_job_export_fails_closed_when_the_job_is_absent(job_state: Path, tmp_path: Path) -> None:
    absent = "00000000-0000-0000-0000-000000000000"
    result = _run(job_state, tmp_path / "exports", "export-evidence", "job", absent)
    assert result.returncode != 0
    assert "job evidence directory missing" in result.stderr


def test_job_export_refuses_a_symlinked_evidence_directory(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (tmp_path / "elsewhere" / JOB_ID).mkdir(parents=True)
    (state / "evidence").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    result = _run(state, tmp_path / "exports", "export-evidence", "job", JOB_ID)
    assert result.returncode != 0
    assert "evidence directory is a symlink" in result.stderr


def test_retained_export_is_unchanged_by_the_job_form(populated_state: Path, tmp_path: Path) -> None:
    result = _run(populated_state, tmp_path / "exports", "export-evidence")
    assert result.returncode == 0, result.stderr
    archive_path = Path(result.stdout.split()[0])
    assert archive_path.name.startswith("retained-evidence-")
