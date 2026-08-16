from __future__ import annotations

import importlib.util
import os
import pwd
import stat
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "demo-real-agent/host/real_agent_demo_secret_preflight.py"
INSTALLER = ROOT / "demo-real-agent/install_secret_preflight.sh"
SUDOERS = ROOT / "demo-real-agent/host/backbond-real-agent-demo-secret-preflight.sudoers"
DEPLOY = ROOT / "demo-real-agent/deploy.sh"

spec = importlib.util.spec_from_file_location("secret_preflight", HELPER)
assert spec and spec.loader
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def secret_file(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "real-agent-demo.env"
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    current = pwd.getpwuid(os.getuid())
    preflight.EXPECTED_UID = current.pw_uid
    preflight.EXPECTED_GID = current.pw_gid
    return path


@pytest.mark.parametrize(
    "judge",
    [
        "DEMO_JUDGE_BACKEND=claude-code-proxy\nCLAUDE_JUDGE_PROXY_TOKEN=proxy-secret\n",
        "OPENROUTER_API_KEY=api-secret\n",
        "ANTHROPIC_API_KEY=api-secret\n",
    ],
)
def test_success_for_each_supported_judge_configuration(tmp_path: Path, judge: str) -> None:
    path = secret_file(
        tmp_path,
        "DEMO_ACCESS_TOKEN=access-secret\nARE_BUNDLE_SIGNING_KEY=signing-secret\n" + judge,
    )
    names = preflight.validate(path)
    assert "DEMO_ACCESS_TOKEN" in names
    assert "ARE_BUNDLE_SIGNING_KEY" in names


@pytest.mark.parametrize("name", ["DEMO_ACCESS_TOKEN", "ARE_BUNDLE_SIGNING_KEY"])
@pytest.mark.parametrize("value", [None, ""])
def test_rejects_missing_or_empty_required_keys(tmp_path: Path, name: str, value: str | None) -> None:
    fields = {"DEMO_ACCESS_TOKEN": "access-secret", "ARE_BUNDLE_SIGNING_KEY": "signing-secret"}
    if value is None:
        fields.pop(name)
    else:
        fields[name] = value
    text = "".join(f"{key}={item}\n" for key, item in fields.items()) + "OPENROUTER_API_KEY=api-secret\n"
    with pytest.raises(preflight.PreflightError, match=name):
        preflight.validate(secret_file(tmp_path, text))


def test_rejects_invalid_judge_configurations(tmp_path: Path) -> None:
    base = "DEMO_ACCESS_TOKEN=a\nARE_BUNDLE_SIGNING_KEY=b\n"
    for judge in ("", "DEMO_JUDGE_BACKEND=claude-code-proxy\n", "DEMO_JUDGE_BACKEND=other\n"):
        with pytest.raises(preflight.PreflightError):
            preflight.validate(secret_file(tmp_path, base + judge))


def test_rejects_wrong_mode_owner_symlink_and_non_regular(tmp_path: Path) -> None:
    path = secret_file(tmp_path, "DEMO_ACCESS_TOKEN=a\nARE_BUNDLE_SIGNING_KEY=b\nOPENROUTER_API_KEY=c\n")
    path.chmod(0o640)
    with pytest.raises(preflight.PreflightError, match="wrong_mode"):
        preflight.validate(path)
    path.chmod(0o600)
    preflight.EXPECTED_UID += 1
    with pytest.raises(preflight.PreflightError, match="wrong_owner"):
        preflight.validate(path)
    preflight.EXPECTED_UID -= 1
    preflight.EXPECTED_GID += 1
    with pytest.raises(preflight.PreflightError, match="wrong_owner"):
        preflight.validate(path)
    preflight.EXPECTED_GID -= 1
    link = tmp_path / "link.env"
    link.symlink_to(path)
    with pytest.raises(preflight.PreflightError, match="not_regular"):
        preflight.validate(link)
    with pytest.raises(preflight.PreflightError, match="not_regular"):
        preflight.validate(tmp_path)


def test_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(preflight.PreflightError, match="missing"):
        preflight.validate(tmp_path / "missing.env")


def test_cli_rejects_arguments_without_reading_secret() -> None:
    result = subprocess.run([str(HELPER), "anything"], text=True, capture_output=True, check=False)
    assert result.returncode == 2
    assert result.stderr == "arguments: rejected\n"


def test_diagnostics_never_contain_values(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = secret_file(
        tmp_path,
        "DEMO_ACCESS_TOKEN=TOP_SECRET_ACCESS\nARE_BUNDLE_SIGNING_KEY=TOP_SECRET_SIGN\nOPENROUTER_API_KEY=TOP_SECRET_API\n",
    )
    for name in preflight.validate(path):
        print(f"{name}: configured")
    output = capsys.readouterr().out
    assert "TOP_SECRET" not in output
    assert "DEMO_ACCESS_TOKEN: configured" in output


def test_fixed_sudoers_scope_and_installer_permissions() -> None:
    sudoers = SUDOERS.read_text(encoding="utf-8").strip()
    assert sudoers == '#1002 ALL=(root) NOPASSWD: /usr/local/sbin/backbond-real-agent-demo-secret-preflight ""'
    installer = INSTALLER.read_text(encoding="utf-8")
    assert 'install -o root -g root -m 0755 "$HELPER_SOURCE" "$HELPER_TARGET"' in installer
    assert 'install -o root -g root -m 0440 "$SUDOERS_SOURCE" "$sudoers_tmp"' in installer
    assert "visudo -cf" in installer
    assert stat.S_IMODE(HELPER.stat().st_mode) & 0o111


def test_deploy_uses_only_fixed_preflight_and_fixed_bind_mount() -> None:
    deploy = DEPLOY.read_text(encoding="utf-8")
    assert 'sudo --non-interactive "$PREFLIGHT_HELPER"' in deploy
    assert 'ENV_FILE="/etc/backbond/real-agent-demo.env"' in deploy
    assert "REAL_AGENT_DEMO_ENV_FILE" not in deploy
    assert "stat -c" not in deploy
    assert 'grep -Eq' not in deploy
    assert 'bash "$SOURCE_DIR/demo-real-agent/install_claude_judge_proxy.sh"' not in deploy
    assert 'source=${ENV_FILE},target=/etc/backbond/real-agent-demo.env,readonly' in deploy
