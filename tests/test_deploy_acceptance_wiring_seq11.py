from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from scripts.deploy import deploy_agent_acceptance as acceptance


ROOT = Path(__file__).resolve().parents[1]


REQUIRED_ENV = {
    "BACKBOND_ACCEPTANCE_TENANT": "11111111-1111-1111-1111-111111111111",
    "BACKBOND_ACCEPTANCE_AGENT": "deploy-acceptance-agent",
    "BACKBOND_ACCEPTANCE_ENDPOINT": "scanner.example",
    "BACKBOND_ACCEPTANCE_SCANNER_VERSION": "0.5.0",
    "BACKBOND_ACCEPTANCE_CORPUS_VERSION": "0.1.0",
    "BACKBOND_ACCEPTANCE_CORPUS_SNAPSHOT_HASH": "sha256:" + "a" * 64,
    "BACKBOND_ACCEPTANCE_SEED": "42",
}


@pytest.mark.parametrize("missing", sorted(REQUIRED_ENV))
def test_acceptance_wrapper_fails_closed_when_binding_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing: str
) -> None:
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    for name, value in REQUIRED_ENV.items():
        if name == missing:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=f"{missing} is required"):
        acceptance.main()


def test_acceptance_wrapper_passes_complete_release_binding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    (credentials / "acceptance-admin-token").write_text("test-only-token", encoding="utf-8")
    database = tmp_path / "scan-log.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE scan_log (scan_id TEXT, agent_id TEXT, forensic_bundle_uri TEXT, snapshot_hash TEXT, probe_set_hash TEXT)"
        )
        connection.execute(
            "INSERT INTO scan_log VALUES (?, ?, ?, ?, ?)",
            ("scan-seq11", "deploy-acceptance-agent", "scanner://bundle", "sha256:snapshot", "sha256:probes"),
        )
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credentials))
    monkeypatch.setenv("BACKBOND_SCAN_LOG_DB", str(database))
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    seen: list[str] = []

    def fake_run(command: list[str], **_kwargs) -> subprocess.CompletedProcess:
        seen.extend(command)
        out = Path(command[command.index("--out") + 1])
        out.write_text(json.dumps({"passed": True, "checks": {"health": {"status": "PASS"}}}), encoding="utf-8")
        Path(f"{out}.envelope.json").write_text(
            json.dumps(
                {
                    "scan_id": "scan-seq11",
                    "status": "complete",
                    "scanner_version": "0.5.0",
                    "corpus_version": "0.1.0",
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(acceptance.subprocess, "run", fake_run)
    assert acceptance.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["passed"] is True
    assert result["scan_log_pins"]["forensic_bundle_uri"] == "scanner://bundle"
    expected_arguments = {
        "--staging-tenant": REQUIRED_ENV["BACKBOND_ACCEPTANCE_TENANT"],
        "--staging-agent": REQUIRED_ENV["BACKBOND_ACCEPTANCE_AGENT"],
        "--staging-endpoint": REQUIRED_ENV["BACKBOND_ACCEPTANCE_ENDPOINT"],
        "--scanner-version": REQUIRED_ENV["BACKBOND_ACCEPTANCE_SCANNER_VERSION"],
        "--corpus-version": REQUIRED_ENV["BACKBOND_ACCEPTANCE_CORPUS_VERSION"],
        "--corpus-snapshot-hash": REQUIRED_ENV["BACKBOND_ACCEPTANCE_CORPUS_SNAPSHOT_HASH"],
        "--seed": REQUIRED_ENV["BACKBOND_ACCEPTANCE_SEED"],
    }
    for flag, value in expected_arguments.items():
        assert seen[seen.index(flag) + 1] == value


def test_acceptance_request_uses_private_tmp_and_operator_wires_all_fields() -> None:
    checks = (ROOT / "scripts" / "deploy" / "acceptance_checks.sh").read_text(encoding="utf-8")
    operator = (ROOT / "scripts" / "deploy" / "operator_push.sh").read_text(encoding="utf-8")

    assert 'REQUEST_DIR="${TMPDIR:-/tmp}"' in checks
    assert "CREDENTIALS_DIRECTORY:-" not in checks
    for flag in (
        "--staging-endpoint",
        "--scanner-version",
        "--corpus-version",
        "--corpus-snapshot-hash",
        "--seed",
    ):
        assert flag in operator
