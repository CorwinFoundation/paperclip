from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
import re
import socket
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "artifacts/BEAAA-23759/v2/backbond-real-agent-demo-secret-preflight"
DEPLOY = ROOT / "demo-real-agent/deploy.sh"


def load_helper():
    loader = importlib.machinery.SourceFileLoader("beaaa23759_v2_helper", str(HELPER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


helper = load_helper()


def parse_status(output: str) -> subprocess.CompletedProcess[str]:
    source = DEPLOY.read_text()
    match = re.search(r"(?ms)^parse_judge_socket_status\(\) \{.*?^\}", source)
    assert match
    script = match.group(0) + '\nparse_judge_socket_status "$1"\n'
    return subprocess.run(["bash", "-c", script, "parser", output], text=True, capture_output=True)


def test_helper_ready_for_socket(tmp_path):
    path = tmp_path / "judge.sock"
    server = socket.socket(socket.AF_UNIX)
    try:
        server.bind(str(path))
        assert helper.judge_socket_status(path) == "ready"
    finally:
        server.close()


def test_helper_missing_for_absent_path(tmp_path):
    assert helper.judge_socket_status(tmp_path / "judge.sock") == "missing"


def test_helper_missing_for_non_socket(tmp_path):
    path = tmp_path / "judge.sock"
    path.write_text("not a socket")
    assert helper.judge_socket_status(path) == "missing"


def test_helper_evaluation_denial_fails_closed():
    class DeniedPath:
        def lstat(self):
            raise PermissionError("denied")

    with pytest.raises(helper.PreflightError, match="JUDGE_SOCKET: evaluation_failed"):
        helper.judge_socket_status(DeniedPath())


@pytest.mark.parametrize(
    ("output", "expected", "success"),
    [
        ("JUDGE_SOCKET: ready", "ready\n", True),
        ("JUDGE_SOCKET: missing", "missing\n", True),
        ("", "", False),
        ("JUDGE_SOCKET: unknown", "", False),
        ("JUDGE_SOCKET: ready\nJUDGE_SOCKET: ready", "", False),
        ("JUDGE_SOCKET: ready\nJUDGE_SOCKET: missing", "", False),
        ("prefix JUDGE_SOCKET: ready", "", False),
    ],
)
def test_deploy_parser_exactly_one_names_only_status(output, expected, success):
    result = parse_status(output)
    assert (result.returncode == 0) is success
    assert result.stdout == expected


def test_repository_change_grants_no_new_host_privilege():
    source = DEPLOY.read_text()
    assert '[[ ! -S "$JUDGE_SOCKET_HOST" ]]' not in source
    for forbidden in ("setfacl", "groupadd", "usermod", "chmod", "chown"):
        assert forbidden not in source
    assert 'sudo --non-interactive "$PREFLIGHT_HELPER"' in source
    assert subprocess.run(["bash", "-n", str(DEPLOY)]).returncode == 0
