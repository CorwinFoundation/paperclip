"""BEAAA-23761: the judge-socket bind source must equal the fixed path the root helper evaluates.

Every test here executes ``demo-real-agent/deploy.sh`` with stubbed ``sudo`` and
``docker`` on PATH and asserts on what the script actually did, not on its source
text. The stubs record every invocation, so "rejected before the root boundary"
is proved by a zero sudo-call count rather than by reading the control flow.

The real root helper is never executed: it holds a one-shot deployment grant that
an execution would consume.
"""

from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "demo-real-agent/deploy.sh"
FIXED_DIR = "/run/backbond-claude-judge"
FIXED_SOCKET = f"{FIXED_DIR}/judge.sock"
INSTALLED_HELPER = Path("/usr/local/sbin/backbond-real-agent-demo-secret-preflight")

PROXY_STATUS = "\n".join(
    [
        "secret_file: valid",
        "DEMO_ACCESS_TOKEN: configured",
        "ARE_BUNDLE_SIGNING_KEY: configured",
        "DEMO_JUDGE_BACKEND: configured",
        "CLAUDE_JUDGE_PROXY_TOKEN: configured",
        "JUDGE_SOCKET: ready",
    ]
)


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


@pytest.fixture
def sandbox(tmp_path):
    """A PATH-isolated harness that runs the shipped deploy.sh without touching live state."""
    work = tmp_path / "work"
    binp = work / "bin"
    binp.mkdir(parents=True)
    (work / "src").mkdir()

    script = DEPLOY
    if not os.access(INSTALLED_HELPER, os.X_OK):
        # The helper is only installed on the passive-demo host. Off-host, relocate the
        # single constant that names it and prove exactly one line changed.
        stub_helper = binp / "stub-preflight-helper"
        _write_exec(stub_helper, "#!/bin/sh\nexit 0\n")
        original = DEPLOY.read_text().splitlines(keepends=True)
        relocated = [
            f'PREFLIGHT_HELPER="{stub_helper}"\n'
            if line.startswith("PREFLIGHT_HELPER=")
            else line
            for line in original
        ]
        assert sum(a != b for a, b in zip(original, relocated)) == 1
        script = work / "deploy.sh"
        script.write_text("".join(relocated))

    _write_exec(
        binp / "sudo",
        f"#!/bin/sh\necho \"$@\" >> {work}/sudo.log\ncat {work}/preflight.out\n"
        f"exit $(cat {work}/preflight.rc)\n",
    )
    _write_exec(
        binp / "docker",
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> {work}/docker.log\n'
        'case "$1" in run) exit 1 ;; esac\nexit 0\n',
    )

    (work / "preflight.out").write_text(PROXY_STATUS + "\n")
    (work / "preflight.rc").write_text("0")

    def run(status=None, rc=None, **env_overrides):
        if status is not None:
            (work / "preflight.out").write_text(status + "\n")
        if rc is not None:
            (work / "preflight.rc").write_text(str(rc))
        env = {
            "PATH": f"{binp}:/usr/bin:/bin",
            "HOME": str(work),
            "DEMO_GIT_SHA": "0" * 40,
            # Defence in depth: if the stub were ever bypassed, the real client
            # cannot reach a daemon and so cannot mutate the host.
            "DOCKER_HOST": "unix:///nonexistent/beaaa23761.sock",
        }
        env.update(env_overrides)
        return subprocess.run(
            ["bash", str(script), str(work / "src")],
            env=env,
            text=True,
            capture_output=True,
        )

    run.work = work
    run.sudo_calls = lambda: len((work / "sudo.log").read_text().splitlines()) if (work / "sudo.log").exists() else 0
    run.docker_calls = lambda: (work / "docker.log").read_text().splitlines() if (work / "docker.log").exists() else []
    return run


def _bind_sources(docker_lines):
    return [
        field.split("source=", 1)[1].split(",", 1)[0]
        for line in docker_lines
        for field in line.split()
        if "target=/run/claude-judge" in field
    ]


def test_default_run_binds_the_fixed_judge_socket_directory(sandbox):
    result = sandbox()
    assert sandbox.sudo_calls() == 1
    assert _bind_sources(sandbox.docker_calls()) == [FIXED_DIR]
    assert result.returncode == 1  # the docker stub aborts the candidate start


def test_override_to_another_directory_is_rejected_before_root_and_docker(sandbox, tmp_path):
    # A real listening socket in the attacker directory, so rejection cannot pass
    # for the wrong reason.
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(attacker / "judge.sock"))
    server.listen(1)
    try:
        result = sandbox(CLAUDE_JUDGE_SOCKET_DIR=str(attacker))
    finally:
        server.close()

    assert result.returncode == 1
    assert "must not override the fixed judge socket directory" in result.stderr
    assert sandbox.sudo_calls() == 0
    assert sandbox.docker_calls() == []


def test_override_set_but_empty_is_rejected(sandbox):
    result = sandbox(CLAUDE_JUDGE_SOCKET_DIR="")
    assert result.returncode == 1
    assert "must not override the fixed judge socket directory" in result.stderr
    assert sandbox.sudo_calls() == 0
    assert sandbox.docker_calls() == []


def test_override_equal_to_the_fixed_directory_is_accepted(sandbox):
    sandbox(CLAUDE_JUDGE_SOCKET_DIR=FIXED_DIR)
    assert sandbox.sudo_calls() == 1
    assert _bind_sources(sandbox.docker_calls()) == [FIXED_DIR]


def test_missing_socket_fails_closed_naming_the_inspected_path(sandbox):
    result = sandbox(status=PROXY_STATUS.replace("JUDGE_SOCKET: ready", "JUDGE_SOCKET: missing"))
    assert result.returncode == 1
    assert FIXED_SOCKET in result.stderr
    assert sandbox.docker_calls() == []


def test_malformed_socket_status_fails_closed(sandbox):
    result = sandbox(status=PROXY_STATUS + "\nJUDGE_SOCKET: ready")
    assert result.returncode == 1
    assert "malformed judge-socket status" in result.stderr
    assert sandbox.docker_calls() == []


def test_helper_failure_fails_closed(sandbox):
    result = sandbox(status="", rc=1)
    assert result.returncode == 1
    assert "secret preflight evaluation failed" in result.stderr
    assert sandbox.docker_calls() == []
