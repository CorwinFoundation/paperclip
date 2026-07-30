import json
import ast
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from harness.vapi_synthetic_capability_adapter import SyntheticCapabilityAdapter

ROOT = Path(__file__).parents[1]
FREEZE = ROOT / "artifacts/beaaa-19218/vapi-2000-probe-authorization-freeze-v1.4.jsonl"
PRIOR = ROOT / "artifacts/beaaa-19220/vapi-2000-authorized-execution-ledger-v1.4.redacted.json"
SCRIPT = ROOT / "scripts/build_beaaa19288_vapi_synthetic_adapter.py"


def run(out):
    subprocess.run([sys.executable, str(SCRIPT), "--freeze", str(FREEZE),
                    "--prior-ledger", str(PRIOR), "--output-dir", str(out)],
                   cwd=ROOT, check=True)


def test_all_596_deterministic_and_network_free(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "create_connection",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network attempted")))
    a, b = tmp_path / "a", tmp_path / "b"
    run(a); run(b)
    assert {p.name: p.read_bytes() for p in a.iterdir()} == {p.name: p.read_bytes() for p in b.iterdir()}
    selection = json.loads((a / "selection-ledger.json").read_text())
    capability = json.loads((a / "capability-analysis.json").read_text())
    ledger = json.loads((a / "dry-run-ledger.json").read_text())
    assert selection["count"] == len(set(selection["stable_case_ids"])) == 596
    assert capability["existing_assistant_executable"] == 0
    assert capability["adapter_required"] == sum(capability["family_counts"].values()) == 596
    assert len(capability["cases"]) == len(ledger["traces"]) == 596
    assert ledger["network_requests"] == ledger["retries"] == 0
    assert ledger["concurrency"] == 1 and ledger["maximum_requests_per_minute"] <= 30
    assert ledger["prior_deny_request_count"] == 453
    assert all(t["network_requests"] == 0 and t["status"] == "dry_run_complete" for t in ledger["traces"])


def test_fail_closed_allowlist_and_namespace_isolation():
    adapter = SyntheticCapabilityAdapter()
    case = {"stable_case_id": "case-a", "family_id": "test", "source_row_sha256": "0" * 64,
            "prompt_turn_sequence": [{"prompt": "synthetic"}]}
    first = adapter.execute(case, ["state"])
    other = dict(case, stable_case_id="case-b")
    second = adapter.execute(other, ["state"])
    assert (first["tenant"], first["session"]) != (second["tenant"], second["session"])
    with pytest.raises(RuntimeError, match="not allowlisted"):
        adapter.execute(dict(case, stable_case_id="case-c"), ["network"])
    with pytest.raises(RuntimeError, match="stopped"):
        adapter.execute(dict(case, stable_case_id="case-d"), ["state"])


def test_implementation_has_no_network_or_credential_surface():
    paths = [ROOT / "harness/vapi_synthetic_capability_adapter.py",
             ROOT / "scripts/build_beaaa19288_vapi_synthetic_adapter.py"]
    forbidden_imports = {"socket", "http", "urllib", "requests", "aiohttp", "vapi"}
    for path in paths:
        tree = ast.parse(path.read_text())
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in (node.names if isinstance(node, ast.Import) else
                          [ast.alias(name=node.module or "")])
        }
        assert not imports & forbidden_imports
        text = path.read_text().lower()
        assert "api_key" not in text and "bearer " not in text
