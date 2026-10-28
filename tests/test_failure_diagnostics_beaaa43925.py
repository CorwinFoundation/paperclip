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


def test_internal_missing_row_code_is_preserved_without_exception_text():
    from api.scan_handlers import OrchestratorError

    reason = _safe_scan_failure_reason(
        OrchestratorError("starter_result_rows_count_mismatch:expected=100:actual=0"),
        stage="result_finalization",
    )
    assert reason.endswith("cause=starter_result_rows_count_mismatch")
    assert "expected=" not in reason
    for exc in (
        RuntimeError("starter_result_rows_count_mismatch:expected=100:actual=0"),
        OrchestratorError("starter_result_rows_count_mismatch:expected=100:actual=0 private-token"),
    ):
        assert _safe_scan_failure_reason(exc, stage="result_finalization").endswith("cause=scan_error")


def test_worker_traceback_redacts_credentials_and_omits_locals():
    from api.scan_handlers import _redacted_worker_diagnostics
    try:
        raise RuntimeError('renderer failed private-token')
    except RuntimeError as exc:
        message, frames = _redacted_worker_diagnostics(exc, secret='private-token')
    assert 'renderer failed' in message
    assert 'private-token' not in message + frames
    assert 'test_worker_traceback_redacts_credentials' in frames
