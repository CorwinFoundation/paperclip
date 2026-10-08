import json
import importlib.util
import subprocess
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "backbond/scan-intake/verify_kaltura_live_acceptance.py"
SPEC = importlib.util.spec_from_file_location("verify_kaltura_live_acceptance", MODULE_PATH)
assert SPEC and SPEC.loader
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


def test_helper_reports_only_acceptance_metadata(monkeypatch, capsys):
    payload = {"ok": True, "anonymous_connection": "passed", "fresh_thread_reset": "passed"}
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, json.dumps(payload), ""))
    assert verify.main() == 0
    assert json.loads(capsys.readouterr().out) == payload


def test_helper_redacts_container_stderr(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 7, "", "private reply"))
    assert verify.main() == 7
    output = capsys.readouterr().out
    assert "private reply" not in output
    assert json.loads(output)["error"] == "live acceptance failed"
