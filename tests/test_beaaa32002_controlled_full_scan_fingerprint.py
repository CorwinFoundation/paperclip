"""BEAAA-32002 exact-fingerprint selection for durable full-scan resume."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from api.full_scan_executor import FullScanJobStore, registry_cases_from_rows
from api.full_scan_job_persistence import FullScanJobPersistence

REPO_ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "beaaa29082_controlled_full_scan_32002",
    REPO_ROOT / "scripts" / "beaaa29082_controlled_full_scan.py",
)
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)


def _cases(suffix: str = "a"):
    return registry_cases_from_rows([
        {
            "test_id": "promptinject",
            "case_id": f"case-{suffix}",
            "status": "active",
            "production_runner_executable": True,
            "risk_or_rubric_id": "P3.1",
        }
    ])


def _fingerprint(**changes):
    value = {
        "agent_id": runner.VAPI_ASSISTANT_ID,
        "scanner_version": "1.3.0",
        "corpus_version": "1.3.0",
        "model_versions": {
            "primary": "target-v1",
            "judge": "judge-v1",
            "runner_candidate_sha": "a" * 40,
        },
        "seed": 42,
        "registry_cases": _cases(),
    }
    value.update(changes)
    return value


def test_exact_fingerprint_reuses_same_job_after_restart(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    store = FullScanJobStore(persistence=FullScanJobPersistence(db))
    expected = _fingerprint()
    job = store.submit(**expected)

    reopened = runner.open_job_store(db)
    assert runner.newest_resumable_job_id(reopened, **expected) == job.job_id


def test_each_fingerprint_mismatch_refuses_stale_job(tmp_path):
    store = FullScanJobStore(
        persistence=FullScanJobPersistence(tmp_path / "jobs.sqlite3")
    )
    expected = _fingerprint()
    store.submit(**expected)

    mismatches = [
        {"agent_id": "different-agent"},
        {"scanner_version": "1.3.1"},
        {"corpus_version": "1.3.1"},
        {"model_versions": {"primary": "target-v2", "judge": "judge-v1"}},
        {"seed": 43},
        {"registry_cases": _cases("b")},
    ]
    for mismatch in mismatches:
        assert runner.newest_resumable_job_id(
            store, **_fingerprint(**mismatch)
        ) is None
