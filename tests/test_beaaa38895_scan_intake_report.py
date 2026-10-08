"""BEAAA-38895 — scan-intake keeps the engine's outcome, verdicts, reasons and pack.

Reproduces live scan 439c7834: engine said incomplete at 43 rows (14 passed,
28 failed, 1 could_not_run) on starter-100@2; the report said finished, 29 fail,
no reasons, starter-100@1.
"""

from __future__ import annotations

import sys
import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backbond" / "scan-intake" / "server"))

import qa_service  # noqa: E402


def test_webhook_accepts_only_truthful_zero_row_terminal_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("QA_DB_PATH", str(tmp_path / "intake.sqlite"))
    import app
    from fastapi.testclient import TestClient

    class Queue:
        calls = []
        def callback(self, scan_id, callback):
            self.calls.append((scan_id, callback))

    queue = Queue()
    monkeypatch.setattr(app, "queue", queue)
    monkeypatch.setattr(app, "WEBHOOK_SECRET", "test-secret")
    client = TestClient(app.app)

    def post(payload):
        raw = json.dumps(payload, separators=(",", ":")).encode()
        signature = hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest()
        return client.post("/scan/v1/scanner-webhook", content=raw,
                           headers={"content-type": "application/json",
                                    "x-backbond-signature": "sha256=" + signature})

    failed = {"scan_id": "remote-s1", "status": "failed", "incomplete": True,
              "incomplete_reason": "scanner_restart_resume_exhausted", "result_rows": []}
    assert post(failed).status_code == 204
    assert queue.calls[0][1]["meta"] == {
        "outcome": "failed", "incomplete_reason": "scanner_restart_resume_exhausted",
        "pack_version": None,
    }
    assert post({"scan_id": "remote-s2", "status": "complete", "result_rows": []}).status_code == 400


def test_customer_target_rejects_backbond_scanner_services(tmp_path, monkeypatch):
    from pydantic import ValidationError

    monkeypatch.setenv("QA_DB_PATH", str(tmp_path / "intake.sqlite"))
    import app

    base = {
        "email": "internal@backbond.ai", "agent_label": "isolation check",
        "transport": "rest", "auth": {"type": "none"},
    }
    for endpoint in (
        "https://backbond.ai/scan/r/d3533c0cff194b7794662d4048e13caf",
        "https://scanner.backbond.ai/internal-fixture",
        "https://api.backbond.ai/scan/v1/scans",
        "https://SCANNER.BACKBOND.AI./internal-fixture",
    ):
        try:
            app.ConnectionFields(**base, endpoint_url=endpoint)
        except ValidationError as exc:
            assert "must not target a BackBond scanner service" in str(exc)
        else:
            raise AssertionError(f"accepted internal service target: {endpoint}")

    assert app.ConnectionFields(**base, endpoint_url="https://agent.example.test/chat")

ROWS = (
    [{"test_id": f"p{i}", "family": "prompt_injection", "prompt": "x", "reply": "y",
      "verdict": "passed", "reason": "declined"} for i in range(14)]
    + [{"test_id": f"f{i}", "family": "jailbreak_roleplay", "prompt": "x", "reply": "y",
        "verdict": "failed", "reason": "it obeyed"} for i in range(28)]
    + [{"test_id": "SEC-02", "family": "secrets_pii_leak", "verdict": "could_not_run",
        "reason": "no reply: RunnerError: customer endpoint timeout after 30.0s"}]
)
PAYLOAD = {"scan_id": "439c7834", "status": "partial", "incomplete": True,
           "incomplete_reason": "customer_endpoint_unreachable_transport",
           "starter_pack_id": "starter-100@2", "result_rows": ROWS}


def _store(tmp_path):
    store = qa_service.IntakeStore(str(tmp_path / "s.sqlite"))
    store.db.execute("PRAGMA foreign_keys=OFF")  # the partner-code row is out of scope here
    store.db.execute(
        "INSERT INTO scans(id,code,partner,email,agent_label,endpoint_host,transport,pack_version,created_at,status)"
        " VALUES('s1','c','p','e@x.test','a','h','http','starter-100@1',?,'running')",
        (datetime.now(timezone.utc).isoformat(),))
    return store


def test_could_not_run_is_its_own_verdict_and_reason_survives():
    tests = qa_service.translate_callback(PAYLOAD)["tests"]
    verdicts = [t["verdict"] for t in tests]
    assert verdicts.count("fail") == 28 and verdicts.count("could_not_run") == 1
    assert all(t.get("reason") for t in tests)


def test_report_keeps_incomplete_pack_reason_and_na_score(tmp_path):
    store = _store(tmp_path)
    token = store.finish("s1", qa_service.translate_callback(PAYLOAD))
    report = store.report(token)
    assert report["outcome"] == "incomplete"
    assert report["incomplete_reason"] == "customer_endpoint_unreachable_transport"
    assert report["pack_version"] == "starter-100@2"
    assert report["starter_score"] is None
    assert report["counts"] == {"passed": 14, "failed": 28, "could_not_run": 1}
    assert all("reason" in t for t in report["tests"])


def test_complete_scan_gets_a_score(tmp_path):
    store = _store(tmp_path)
    done = dict(PAYLOAD, status="complete", incomplete=False, incomplete_reason=None)
    report = store.report(store.finish("s1", qa_service.translate_callback(done)))
    assert report["outcome"] == "complete" and report["starter_score"] == round(100 * 14 / 42, 1)


def test_zero_row_terminal_failure_releases_slot_and_free_scan_quota(tmp_path):
    store = _store(tmp_path)
    failure = {"scan_id": "remote-s1", "status": "failed", "incomplete": True,
               "incomplete_reason": "scanner_restart_resume_exhausted", "result_rows": []}
    token = store.finish("s1", qa_service.translate_callback(failure))
    report = store.report(token)
    assert report["outcome"] == "failed"
    assert report["tests"] == [] and report["starter_score"] is None

    status_token = "minimum-capability-status-token"
    store.db.execute("UPDATE scans SET status_token_hash=? WHERE id='s1'",
                     (qa_service.hashlib.sha256(status_token.encode()).hexdigest(),))
    assert store.status(status_token)["status"] == "error"
    assert store.status(status_token)["retry_allowed"] is True

    store.put_code(qa_service.Code("c", "p", "Partner", "standard", 1, 1,
                                  "2099-01-01"))
    replacement = store.reserve(code="c", email="e@x.test", agent_label="replacement",
                                endpoint_url="https://agent.example.test/chat", transport="rest")
    assert replacement["scan_id"] != "s1"


def test_legacy_list_finish_still_works(tmp_path):
    store = _store(tmp_path)
    report = store.report(store.finish("s1", qa_service.translate_tests(PAYLOAD)))
    assert report["outcome"] == "complete" and report["pack_version"] == "starter-100@1"


def test_variable_rows_survive_callback_storage_and_stay_owner_scoped(tmp_path):
    store = _store(tmp_path)
    store.db.execute(
        "INSERT INTO scans(id,code,partner,email,agent_label,endpoint_host,transport,pack_version,created_at,status)"
        " VALUES('s2','c','other','other@x.test','other-agent','other.test','http','starter-100@4',?,'running')",
        (datetime.now(timezone.utc).isoformat(),),
    )
    rows = [{
        "test_id": "case-1", "family": "prompt_injection",
        "pack_id": "starter-100@4", "form_id": "owner-one-form",
        "prompt": "fixture request [REDACTED]", "reply": "fixture reply [REDACTED]",
        "verdict": "failed", "reason": "followed injected instructions",
    }]
    callback = qa_service.translate_callback({
        "status": "complete", "starter_pack_id": "starter-100@4", "result_rows": rows,
    })
    owner_one = store.report(store.finish("s1", callback))
    owner_two = store.report(store.finish("s2", {"tests": [], "meta": {"outcome": "complete"}}))

    assert owner_one["tests"][0]["prompt"] == "fixture request [REDACTED]"
    assert owner_one["tests"][0]["reply"] == "fixture reply [REDACTED]"
    # Redaction happens in the runner before the callback; see
    # tests/test_starter_variable_walker.py (credential-value -> [REDACTED]).
    assert owner_two["tests"] == []
    assert "owner-one-form" not in str(owner_two)


def test_tracked_app_keeps_qa_preview_routes_retired():
    # Live AriClaw retired all nine /qa-preview/ decorators; a reinstall must not bring them back.
    app = (Path(__file__).resolve().parents[1] / "backbond" / "scan-intake" / "server" / "app.py").read_text()
    assert '("/qa-preview/' not in app


def test_old_report_rows_without_evidence_render_the_fallback_not_blank_boxes():
    # BEAAA-39292 check 5: translate_tests writes prompt/reply as "" for old rows,
    # so the report must gate on non-empty text, not on key presence.
    old = qa_service.translate_tests({"result_rows": [{"test_id": "old-1", "verdict": "failed", "reason": "r"}]})
    assert old[0]["prompt"] == "" and old[0]["reply"] == ""
    js = (Path(__file__).resolve().parents[1] / "backbond" / "operations" / "site-quickfix"
          / "release-2026-10-07-starter-v2" / "site" / "scan" / "report.js").read_text()
    assert "Object.hasOwn(test, 'prompt')" not in js
    assert "if (!prompt.trim() && !reply.trim())" in js
    assert "Request and reply text are unavailable for this test." in js
