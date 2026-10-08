from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


SERVER = Path(__file__).resolve().parents[1] / "backbond" / "scan-intake" / "server"
sys.path.insert(0, str(SERVER))
os.environ.setdefault("QA_DB_PATH", str(Path(tempfile.gettempdir()) / f"beaaa39467-{os.getpid()}.sqlite"))

import app  # noqa: E402
import qa_service  # noqa: E402


def test_live_status_projects_real_engine_intermediate_and_terminal_progress(monkeypatch, tmp_path):
    intake = qa_service.IntakeStore(str(tmp_path / "intake.sqlite"))
    intake.put_code(qa_service.Code("CODE", "owner", "Owner", "standard", 1, 1, "2099-01-01"))
    token = "live-progress-status-token"
    intake.db.execute(
        """INSERT INTO scans
        (id,code,partner,email,agent_label,endpoint_host,transport,pack_version,
         created_at,status,scanner_id,status_token_hash)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("local-1", "CODE", "owner", "owner@example.test", "Agent", "agent.example.test",
         "rest", "starter-100@4", "2026-10-08T18:00:00+00:00", "running", "engine-1",
         hashlib.sha256(token.encode()).hexdigest()),
    )
    payloads = iter((
        {"state": "running", "phase": "execution", "stage_label": "Evidence checks",
         "processed_count": 17, "accepted_pack_total": 100, "usable_count": 15,
         "unavailable_count": 2, "timing": {"elapsed_seconds": 22.0,
         "queue_seconds": 1.0, "execution_seconds": 21.0,
         "estimated_remaining_seconds": {"low": 82.0, "high": 123.0}},
         "current_test_id": "starter.case-017", "current_family": "prompt-injection",
         "last_activity_at": "2026-10-08T18:00:21+00:00",
         "heartbeat_at": "2026-10-08T18:00:22+00:00", "updated_at": "2026-10-08T18:00:22+00:00",
         "evidence_rows": [{"secret": "must not escape"}]},
        {"state": "finished", "phase": "report_preparation", "stage_label": "Preparing report",
         "processed_count": 100, "accepted_pack_total": 100, "usable_count": 96,
         "unavailable_count": 4, "timing": {"elapsed_seconds": 130.0,
         "queue_seconds": 1.0, "execution_seconds": 129.0,
         "estimated_remaining_seconds": None}, "updated_at": "2026-10-08T18:02:10+00:00"},
    ))
    calls = []

    class Response:
        status_code = 200
        def __init__(self):
            self._payload = next(payloads)
        def json(self):
            return self._payload

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["timeout"] == 5
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def get(self, url, headers):
            calls.append((url, headers))
            return Response()

    monkeypatch.setattr(app, "store", intake)
    monkeypatch.setattr(app.httpx, "Client", Client)
    monkeypatch.setattr(app, "_headers", lambda data=None: {"Authorization": "Bearer owner-token"})
    client = TestClient(app.app)

    intermediate = client.post("/scan/v1/scan-status", json={"status_token": token})
    terminal = client.post("/scan/v1/scan-status", json={"status_token": token})

    assert intermediate.status_code == terminal.status_code == 200
    assert intermediate.json()["processed_count"] == 17
    assert intermediate.json()["accepted_pack_total"] == 100
    assert intermediate.json()["timing"]["queue_seconds"] == 1.0
    assert intermediate.json()["current_test_id"] == "starter.case-017"
    assert intermediate.json()["current_family"] == "prompt-injection"
    assert intermediate.json()["last_activity_at"] == "2026-10-08T18:00:21+00:00"
    assert "evidence_rows" not in intermediate.json()
    assert terminal.json()["status"] == "finished"
    assert terminal.json()["usable_count"] == 96
    assert terminal.json()["unavailable_count"] == 4
    assert calls == [
        (f"{app.SCANNER_URL}/scans/engine-1/progress", {"Authorization": "Bearer owner-token"}),
        (f"{app.SCANNER_URL}/scans/engine-1/progress", {"Authorization": "Bearer owner-token"}),
    ]
