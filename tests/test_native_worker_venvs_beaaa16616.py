"""Real-package smoke tests for the ADR-0056 framework worker venvs."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
VENVS = Path(os.environ.get("ARE_EVAL_VENVS_DIR", "/opt/are-scanner/eval-venvs"))


def _run(worker_id: str, row: dict, *, seed: int) -> dict:
    python = VENVS / f"{worker_id}-venv" / "bin" / "python"
    if not python.exists():
        pytest.skip(f"{worker_id} isolated venv not built at {python}")
    payload = {
        "worker_id": worker_id,
        "registry_row": row,
        "job": {
            "job_id": f"native-{worker_id}-scan",
            "scanner_version": "0.5.0",
            "corpus_version": "scanner-test-registry-baseline-2026-07-13",
            "model_versions": {"primary": "controlled-local-target@1"},
            "seed": seed,
        },
    }
    proc = subprocess.run(
        [str(python), "-m", "tools.framework_workers", "run"],
        input=json.dumps(payload), text=True, capture_output=True, cwd=REPO, timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.splitlines()[-1])


def test_real_agentdojo_0135_suite_runtime_and_native_verifier():
    response = (
        "emma.johnson@bluesparrowtech.com "
        "networking.participants@industry-network.com "
        "alex.williams@mainsail-tech.com"
    )
    result = _run(
        "agentdojo",
        {
            "test_id": "agentdojo",
            "case_id": "workspace.user_task_0",
            "agentdojo_suite": "workspace",
            "agentdojo_task_id": "user_task_0",
            "agentdojo_task_kind": "user",
            "agentdojo_benchmark_version": "v1.2.2",
            "subject_adapter": {
                "response": response,
                "tool_calls": [{
                    "function": "search_calendar_events",
                    "args": {"query": "Networking event", "date": "2024-05-26"},
                }],
            },
            "evidence_uri_template": "evidence://{scan_id}/{test_id}/{case_id}.json",
        },
        seed=16616,
    )
    assert result["status"] == "complete", result
    assert result["evidence_ref"].endswith("/agentdojo/workspace.user_task_0.json")


def test_real_garak_0100_probe_generator_detector_and_seed():
    result = _run(
        "garak",
        {
            "test_id": "garak",
            "case_id": "garak.test.Blank",
            "garak_probe": "test.Blank",
            "subject_adapter": {"response": "controlled local response"},
            "evidence_uri_template": "evidence://{scan_id}/{test_id}/{case_id}.json",
        },
        seed=16616,
    )
    assert result["status"] == "complete", result
    assert result["evidence_ref"].endswith("/garak/garak.test.Blank.json")


def test_real_inspect_03220_eval_target_model_and_terminal_verdict():
    result = _run(
        "inspect",
        {
            "test_id": "p1_1_factuality_golden",
            "case_id": "inspect.native.venv.case_001",
            "risk_or_rubric_id": "P1.1",
            "prompt_or_action": "answer the controlled Inspect venv case",
            "expected_response": "INSPECT_VENV_TERMINAL_OK",
            "subject_adapter": {"response": "INSPECT_VENV_TERMINAL_OK"},
            "evidence_uri_template": "evidence://{scan_id}/{test_id}/{case_id}.json",
        },
        seed=16615,
    )
    assert result["status"] == "complete", result
    assert result["evidence_ref"].endswith("/p1_1_factuality_golden/inspect.native.venv.case_001.json")


def test_real_pyrit_060_orchestrator_target_and_terminal_verdict():
    result = _run(
        "pyrit",
        {
            "test_id": "pyrit",
            "case_id": "pyrit.native.venv.case_001",
            "risk_or_rubric_id": "P3.1",
            "prompt_or_action": "run pinned PyRIT against the controlled subject",
            "expected_response": "PYRIT_VENV_TERMINAL_OK",
            "subject_adapter": {"response": "PYRIT_VENV_TERMINAL_OK"},
            "evidence_uri_template": "evidence://{scan_id}/{test_id}/{case_id}.json",
        },
        seed=16615,
    )
    assert result["status"] == "complete", result
    assert result["evidence_ref"].endswith("/pyrit/pyrit.native.venv.case_001.json")
