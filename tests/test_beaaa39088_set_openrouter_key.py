"""BEAAA-39088: the one root step that rotates the scan lane's OpenRouter key."""

import hashlib
import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "scan-schedule" / "set-openrouter-key.sh"


def _run(env_file: Path, key_file: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, BACKBOND_SCAN_ENV_FILE=str(env_file))
    return subprocess.run(
        ["sh", str(SCRIPT), str(key_file)], env=env, capture_output=True, text=True
    )


def test_replaces_only_the_key_line_idempotently(tmp_path: Path) -> None:
    env_file = tmp_path / "scan-schedule.env"
    env_file.write_text("A=1\nOPENROUTER_API_KEY=sk-or-old\nB=2\n", encoding="utf-8")
    env_file.chmod(0o600)
    key_file = tmp_path / "key"
    key_file.write_text("sk-or-new\n", encoding="utf-8")

    for _ in range(2):
        result = _run(env_file, key_file)
        assert result.returncode == 0, result.stderr

    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert lines == ["A=1", "B=2", "OPENROUTER_API_KEY=sk-or-new"]
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert "sk-or-new" not in result.stdout
    assert hashlib.sha256(b"sk-or-new").hexdigest()[:12] in result.stdout


def test_refuses_a_file_that_is_not_an_openrouter_key(tmp_path: Path) -> None:
    env_file = tmp_path / "scan-schedule.env"
    env_file.write_text("OPENROUTER_API_KEY=sk-or-old\n", encoding="utf-8")
    key_file = tmp_path / "key"
    key_file.write_text("not-a-key", encoding="utf-8")

    result = _run(env_file, key_file)

    assert result.returncode == 2
    assert env_file.read_text(encoding="utf-8") == "OPENROUTER_API_KEY=sk-or-old\n"
