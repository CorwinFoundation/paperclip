"""BEAAA-42361: read-only accepted-store fallback for smoke/dev scans."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

from api.accepted_scan_store import _SCHEMA_SQL


HELPER = Path(__file__).resolve().parents[1] / "scripts/deploy/scanner-deploy.sh"
SCAN_ID = "3bf9027c-7c19-4feb-b715-001c0a98b48f"
CLIENT_REQUEST_ID = "qa38869-72cff117-8900-442d-9b21-6118b199e4d7"
REQUEST_HASH = "sha256:" + "c" * 64
SECRETS = (
    "tenant-must-not-leak", "agent-must-not-leak", "https://target.invalid/hook",
    "prompt-must-not-leak", "response-must-not-leak", "upstream said 404 Not Found",
    "whsec-must-not-leak", "request_json", "context_json",
)


def _full_scan_store(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE full_scan_jobs (job_id TEXT, job_json TEXT)")
    conn.execute("CREATE TABLE full_scan_idempotency (idempotency_key TEXT, job_id TEXT)")
    conn.commit()
    conn.close()


def _smoke_store(path: Path, *, client_request_id: str = CLIENT_REQUEST_ID,
                 identity_tenant: str = "tenant-must-not-leak") -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA_SQL)
    request = {"scan_id": SCAN_ID, "tenant_id": "tenant-must-not-leak",
               "endpoint": "https://target.invalid/hook", "pack_id": "starter-100@5",
               "prompt": "prompt-must-not-leak"}
    context = {"agent_id": "agent-must-not-leak",
               "webhook_cfg": {"url": "https://target.invalid/hook", "secret": "whsec-must-not-leak"}}
    result = {"scan_id": SCAN_ID, "status": "complete", "scanner_version": "0.5.0",
              "corpus_version": "starter-100@5", "rows_emitted": 100,
              "incomplete": False, "incomplete_reason": "upstream said 404 Not Found",
              "starter_result_rows": [{"reply": "response-must-not-leak"}] * 100}
    conn.execute(
        "INSERT INTO accepted_scans (scan_id, tenant_id, state, accepted_at, updated_at, "
        "resumable, resume_count, request_json, context_json, result_json, completed_test_count) "
        "VALUES (?, ?, 'terminal', 1.0, 2.0, 1, 0, ?, ?, ?, 100)",
        (SCAN_ID, "tenant-must-not-leak", json.dumps(request), json.dumps(context), json.dumps(result)),
    )
    conn.execute(
        "INSERT INTO scan_request_identity VALUES (?, ?, ?, ?, '{}', 1.0)",
        (identity_tenant, client_request_id, REQUEST_HASH, SCAN_ID),
    )
    for i in range(100):
        state, error = ("finished", None) if i < 33 else ("failed", "target")
        conn.execute(
            "INSERT INTO scan_case_progress VALUES (?, ?, 'family-x', ?, 1.0, 2.0, 2.0, ?, ?)",
            (SCAN_ID, f"case-{i:03d}", state, 1 if i == 99 else 0, error),
        )
    conn.commit()
    conn.close()


def _run(state: Path, scan_id: str = SCAN_ID) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(HELPER), "evidence-extract", scan_id],
        env={**os.environ, "SCANNER_EVIDENCE_STATE_DIR": str(state),
             "SCANNER_FULL_SCAN_JOB_STORE": str(state / "full_scan_jobs.sqlite3"),
             "SCANNER_ACCEPTED_SCAN_STORE": str(state / "accepted_scans.sqlite3")},
        text=True, capture_output=True, check=False,
    )


def _state(tmp_path: Path, **kwargs) -> Path:
    state = tmp_path / "state"
    state.mkdir()
    _full_scan_store(state / "full_scan_jobs.sqlite3")
    _smoke_store(state / "accepted_scans.sqlite3", **kwargs)
    return state


def test_smoke_scan_projects_bounded_accepted_store_state(tmp_path: Path) -> None:
    state = _state(tmp_path)
    result = _run(state)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["schema_version"] == "scanner-evidence-extract-accepted@1"
    assert out["scan_id"] == SCAN_ID
    assert out["accepted"] == {"state": "terminal", "resumable": True, "resume_count": 0,
                               "completed_test_count": 100}
    assert out["request_identity"] == {
        "present": 1, "client_request_id": CLIENT_REQUEST_ID,
        "canonical_request_hash": REQUEST_HASH, "scan_id_matches": True,
        "tenant_binding_matches": True,
    }
    assert out["case_progress"] == {
        "rows": 100, "by_state": {"failed": 67, "finished": 33},
        "by_error_category": {"target": 67}, "retry_count_total": 1,
        "usable": 33, "unavailable": 67,
    }
    assert out["result"] == {
        "status": "complete", "scan_id_matches": True, "scanner_version": "0.5.0",
        "corpus_version": "starter-100@5", "rows_emitted": 100, "starter_result_rows": 100,
        "incomplete": False, "incomplete_reason_category": "other",
    }
    # The retained-scan completion-envelope defect is surfaced, not rewritten.
    assert out["consistency"] == {"completed_test_count_equals_usable": False,
                                  "result_incomplete_flag_matches_unavailable": False}
    for forbidden in SECRETS:
        assert forbidden not in result.stdout + result.stderr


def test_accepted_fallback_never_writes_either_store(tmp_path: Path) -> None:
    state = _state(tmp_path)
    stores = [state / "full_scan_jobs.sqlite3", state / "accepted_scans.sqlite3"]
    before = [p.read_bytes() for p in stores]
    first, second = _run(state), _run(state)
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    assert [p.read_bytes() for p in stores] == before
    assert sorted(p.name for p in state.iterdir()) == sorted(p.name for p in stores)


def test_nonconforming_client_request_id_and_tenant_mismatch_are_flagged(tmp_path: Path) -> None:
    state = _state(tmp_path, client_request_id="prompt-must-not-leak <script>",
                   identity_tenant="other-tenant-must-not-leak")
    result = _run(state)
    assert result.returncode == 0, result.stderr
    identity = json.loads(result.stdout)["request_identity"]
    assert identity["client_request_id"] == "nonconforming"
    assert identity["tenant_binding_matches"] is False
    assert "other-tenant-must-not-leak" not in result.stdout
    assert "prompt-must-not-leak" not in result.stdout


def test_unknown_scan_with_real_schema_still_reports_not_found(tmp_path: Path) -> None:
    state = _state(tmp_path)
    missing = _run(state, "00000000-0000-0000-0000-000000000000")
    assert missing.returncode != 0
    assert "scan id not found" in missing.stderr
    counts = json.loads(missing.stderr.splitlines()[0])["counts"]
    assert counts["accepted_scan_matches"] == 0
