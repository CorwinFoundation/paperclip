#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import importlib.util
import io
import pathlib
import tempfile
import types
import unittest
import urllib.error


AUDIT_PATH = pathlib.Path(__file__).with_name("audit.py")


def load_audit() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("agent_env_audit", AUDIT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def agent(
    *,
    adapter_type: str = "claude_local",
    env: dict[str, str] | None = None,
    agent_id: str = "agent-123456789",
    name: str = "Claude Agent",
) -> dict:
    return {
        "id": agent_id,
        "name": name,
        "adapterType": adapter_type,
        "adapterConfig": {"env": env or {}},
        "spentMonthlyCents": 0,
        "status": "idle",
    }


class AgentEnvAuditTest(unittest.TestCase):
    def run_main(
        self,
        module: types.ModuleType,
        *,
        agents: list[dict] | None = None,
        proc_findings: list[dict] | None = None,
    ) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as td:
            module.REPORT_DIR = pathlib.Path(td)
            module.fetch_agents = lambda: agents if agents is not None else []
            module.scan_proc = lambda: proc_findings if proc_findings is not None else []
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = module.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def test_clean_registry_and_processes_exit_zero(self) -> None:
        audit = load_audit()
        code, stdout, stderr = self.run_main(
            audit,
            agents=[agent(env={"CODEX_HOME": "/tmp/codex"}), agent(adapter_type="codex_local")],
            proc_findings=[{"pid": 101, "leak": False, "agent_id_hint": "agent-123456789"}],
        )

        self.assertEqual(code, 0)
        self.assertIn("Registry clean", stdout)
        self.assertIn("# report:", stderr)

    def test_registry_leak_exits_one_without_secret_value(self) -> None:
        audit = load_audit()
        code, stdout, _stderr = self.run_main(
            audit,
            agents=[
                agent(
                    env={"ANTHROPIC_API_KEY": "sk-ant-secret-value", "CODEX_HOME": "/tmp/codex"},
                    name="Leaky Claude",
                )
            ],
        )

        self.assertEqual(code, 1)
        self.assertIn("**LEAK DETECTED.**", stdout)
        self.assertIn("ANTHROPIC_API_KEY", stdout)
        self.assertNotIn("sk-ant-secret-value", stdout)

    def test_stale_claude_process_leak_exits_one_without_secret_value(self) -> None:
        audit = load_audit()
        with tempfile.TemporaryDirectory() as td:
            proc = pathlib.Path(td)
            pid = proc / "123"
            pid.mkdir()
            (pid / "comm").write_text("claude")
            (pid / "environ").write_bytes(
                b"PAPERCLIP_AGENT_ID=agent-abcdefghi\x00"
                b"ANTHROPIC_API_KEY=sk-ant-stale-process\x00"
            )
            audit.PROC_DIR = proc
            findings = audit.scan_proc()

        code, stdout, _stderr = self.run_main(audit, agents=[agent()], proc_findings=findings)

        self.assertEqual(code, 1)
        self.assertEqual(findings, [{"pid": 123, "leak": True, "agent_id_hint": "agent-abcdefghi"}])
        self.assertIn("**LEAK DETECTED.**", stdout)
        self.assertNotIn("sk-ant-stale-process", stdout)

    def test_api_unreachable_exits_two(self) -> None:
        audit = load_audit()

        def fail_fetch() -> list[dict]:
            raise urllib.error.URLError("connection refused")

        audit.fetch_agents = fail_fetch
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = audit.main()

        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("cannot reach Paperclip API", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
