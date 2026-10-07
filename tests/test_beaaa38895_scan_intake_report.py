"""BEAAA-38895 — scan-intake keeps the engine's outcome, verdicts, reasons and pack.

Reproduces live scan 439c7834: engine said incomplete at 43 rows (14 passed,
28 failed, 1 could_not_run) on starter-100@2; the report said finished, 29 fail,
no reasons, starter-100@1.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backbond" / "scan-intake" / "server"))

import qa_service  # noqa: E402

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


def test_legacy_list_finish_still_works(tmp_path):
    store = _store(tmp_path)
    report = store.report(store.finish("s1", qa_service.translate_tests(PAYLOAD)))
    assert report["outcome"] == "complete" and report["pack_version"] == "starter-100@1"
