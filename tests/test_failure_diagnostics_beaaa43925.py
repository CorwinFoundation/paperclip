from types import SimpleNamespace

from api.scan_handlers import _safe_scan_failure_reason


def test_transport_diagnostics_exclude_echoed_credentials():
    exc = ConnectionError("target echoed Authorization: Bearer private-token")
    exc.response = SimpleNamespace(status_code=503, text="private-token")
    assert _safe_scan_failure_reason(exc, stage="case_execution") == (
        "scan_worker_error:stage=case_execution:cause=transport_error:http_status=503")


def test_untrusted_status_and_exception_message_are_not_persisted():
    exc = RuntimeError("private-token")
    exc.response = SimpleNamespace(status_code="private-token")
    assert _safe_scan_failure_reason(exc, stage="result_finalization") == (
        "scan_worker_error:stage=result_finalization:cause=scan_error")
