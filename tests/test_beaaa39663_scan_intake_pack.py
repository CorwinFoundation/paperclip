from __future__ import annotations

import os
import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).parents[1]
SERVER = ROOT / "backbond" / "scan-intake" / "server"
sys.path.insert(0, str(SERVER))
_fd, _db_path = tempfile.mkstemp(prefix="beaaa39663-intake-", suffix=".sqlite")
os.close(_fd)
os.unlink(_db_path)
os.environ["QA_DB_PATH"] = _db_path

import app  # noqa: E402
import qa_service  # noqa: E402

PATCHER_PATH = (ROOT / "backbond" / "operations" / "site-quickfix" /
                "release-2026-10-09-voice-pack-override" / "patch_intake_pack.py")
_spec = importlib.util.spec_from_file_location("beaaa39663_patcher", PATCHER_PATH)
patcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(patcher)


BASE = {
    "code": "PARTNER",
    "email": "owner@example.test",
    "agent_label": "Training portal",
    "endpoint_url": "https://agent.example.test/chat",
    "transport": "rest",
    "auth": {"type": "none"},
}


def test_connection_and_scan_models_leave_installed_default_untouched():
    assert app.ConnectionFields(**BASE).pack_id is None
    assert app.ScanRequest(**BASE, consent_owner=True, consent_terms=True).pack_id is None
    payload = qa_service.scanner_payload(
        {**BASE, "partner": "partner"}, callback_url="https://callback.test",
        callback_secret_ref="test-ref",
    )
    assert payload["pack_id"] == "starter-100@5"


def test_invalid_pack_is_rejected_by_both_intake_endpoints():
    with pytest.raises(ValidationError):
        app.ConnectionFields(**BASE, pack_id="starter-unknown@9")
    with pytest.raises(ValidationError):
        app.ScanRequest(**BASE, pack_id="starter-unknown@9", consent_owner=True, consent_terms=True)

    client = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(app.app)
    for path, body in (
        ("/scan/v1/connection-checks", {**BASE, "pack_id": "starter-unknown@9"}),
        ("/scan/v1/scans", {**BASE, "pack_id": "starter-unknown@9", "consent_owner": True, "consent_terms": True}),
    ):
        response = client.post(path, json=body)
        assert response.status_code == 422
        assert response.json() == {"error": "Check the submitted fields and required consent."}


def test_voice_pack_is_forwarded_persisted_and_rendered(tmp_path, monkeypatch):
    monkeypatch.setenv("QA_DB_PATH", str(tmp_path / "intake.sqlite"))
    store = qa_service.IntakeStore(str(tmp_path / "store.sqlite"))
    store.put_code(qa_service.Code("PARTNER", "partner", "Partner", "standard", 5, 2, "2099-01-01"))
    reservation = store.reserve(
        code="PARTNER", email=BASE["email"], agent_label=BASE["agent_label"],
        endpoint_url=BASE["endpoint_url"], transport=BASE["transport"],
        pack_version="starter-voice-50@1", terms_version="terms-v1",
    )
    row = store.scan(reservation["scan_id"])
    assert row["pack_version"] == "starter-voice-50@1"

    payload = qa_service.scanner_payload(
        {**BASE, "partner": "partner", "pack_id": "starter-voice-50@1"},
        callback_url="https://api.example.test/scan/v1/scanner-webhook",
        callback_secret_ref="test-ref",
    )
    assert payload["pack_id"] == "starter-voice-50@1"

    store.db.execute("UPDATE scans SET status='running' WHERE id=?", (reservation["scan_id"],))
    token = store.finish(reservation["scan_id"], {"tests": [], "meta": {"outcome": "complete"}})
    assert store.report(token)["pack_version"] == "starter-voice-50@1"
    report_page = (ROOT / "backbond" / "operations" / "site-quickfix"
                   / "release-2026-10-07-starter-v2" / "site" / "scan" / "report.js").read_text()
    assert "starter-voice-50@1" in report_page


def test_live_patcher_preserves_installed_at3_default_and_is_idempotent(tmp_path):
    server = tmp_path / "server"
    server.mkdir()
    app_source = '''PUBLIC_PARTNER = "public"
DEFAULT = "starter-100@3"
class ConnectionFields:
    vapi_assistant_id: str | None = None
    def consent_and_transport(self):
        return self
def launch(body, store):
    reservation = store.reserve(code="x", email="x", agent_label="x", endpoint_url="x",
            transport="rest", terms_version="v",
            improvement_consent=body.consent_improvement)
'''
    service_source = '''class IntakeStore:
    def reserve(self, *, code: str, email: str, agent_label: str,
                endpoint_url: str, transport: str, now: str | None = None,
                terms_version: str | None = None, improvement_consent: bool = False) -> dict:
        created = now
        values = tuple((code, email,
               "starter-100@3", created))
        return {"values": values}
def scanner_payload(data):
    return {
            "pack_id": "starter-100@3", "include_result_rows": True,
    }
'''
    (server / "app.py").write_text(app_source)
    (server / "qa_service.py").write_text(service_source)
    backup = tmp_path / "backup"
    assert patcher.patch_pair(server / "app.py", server / "qa_service.py", backup) is True
    app_updated = (server / "app.py").read_text()
    service_updated = (server / "qa_service.py").read_text()
    assert 'DEFAULT_STARTER_PACK_ID = "starter-100@3"' in app_updated
    assert "pack_id: str | None" in app_updated
    assert 'data.get("pack_id") or "starter-100@3"' in service_updated
    assert 'pack_version or "starter-100@3"' in service_updated
    assert (backup / "app.py").stat().st_mode & 0o777 == 0o600
    assert patcher.patch_pair(server / "app.py", server / "qa_service.py", backup) is False


@pytest.mark.parametrize('selected', [None, 'starter-100@1', 'starter-100@2', 'starter-100@3', 'starter-100@4', 'starter-100@5', 'starter-voice-50@1'])
def test_installed_new_scan_manifest_and_scanner_request_identity(selected, tmp_path, monkeypatch):
    """Exercise installed intake and real Scanner entry without model/network calls."""
    from api.scan_handlers import ScanRuntime, scan_handler
    from scanners.live_probe_orchestrator import ScanResult
    monkeypatch.setenv('ARE_SCAN_TIER', 'on')
    monkeypatch.delenv('BACKBOND_STARTER_PACK_LEGACY_V1', raising=False)
    expected = selected or 'starter-100@5'
    body = app.ScanRequest(**BASE, pack_id=selected, consent_owner=True, consent_terms=True)
    payload = qa_service.scanner_payload(
        {**body.model_dump(), 'partner': 'partner'},
        callback_url='https://callback.test', callback_secret_ref='test-ref')
    captured = {}
    def run(request, secret):
        captured['request'] = request
        return ScanResult(scan_id=request.scan_id, status='complete',
                          scanner_version=request.scanner_version, corpus_version=request.corpus_version,
                          rows_emitted=50 if 'voice' in expected else 100, k_replicates=request.k_replicates,
                          starter_result_rows=tuple(
                              {'test_id': f'offline-{i}', 'pack_id': expected,
                               'family': 'answers_normal_requests', 'prompt': 'Offline request',
                               'reply': 'Offline reply', 'verdict': 'passed'}
                              for i in range(50 if 'voice' in expected else 100)))
    status, manifest = scan_handler(payload, tenant_header=payload['tenant_id'],
                                   runtime=ScanRuntime(run_scan_fn=run), scan_id_factory=lambda: 'new-starter')
    assert status == 202
    assert manifest['pack_id'] == captured['request'].pack_id == expected
    store = qa_service.IntakeStore(str(tmp_path / 'new.sqlite'))
    store.put_code(qa_service.Code('PARTNER', 'partner', 'Partner', 'standard', 5, 2, '2099-01-01'))
    reservation = store.reserve(code='PARTNER', email=BASE['email'], agent_label=BASE['agent_label'],
                                endpoint_url=BASE['endpoint_url'], transport='rest', pack_version=selected)
    assert store.scan(reservation['scan_id'])['pack_version'] == expected
    (tmp_path / 'new-scan-manifest.json').write_text(__import__('json').dumps(manifest, default=str))
