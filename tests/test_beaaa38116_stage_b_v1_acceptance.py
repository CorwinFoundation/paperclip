import importlib.util
import io
import json
from pathlib import Path

import pytest

from stage_b_pilot.instinct_standing import StandingReportStore


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "ops/stage-b-inbound/accept-v1-report.py"


def _module():
    spec = importlib.util.spec_from_file_location("accept_v1", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class _Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_behavioral_acceptance_and_retry_preserve_original_rows(tmp_path, monkeypatch):
    module = _module()
    db_path = tmp_path / "standing.sqlite3"
    store = StandingReportStore(db_path)
    issued = store.issue("pilot-account", "agent@example.test", followup_enabled=True)
    original_ids = []
    for number in range(6):
        row = store.issue_followup("pilot-account", issued["grant_id"], f"Original {number}")
        original_ids.append(row["request_id"])
    token_file = tmp_path / "grant.token"
    token_file.write_text(issued["token"])
    token_file.chmod(0o600)
    config = tmp_path / "stage-b.env"
    config.write_text("BB_HISTORY_EXTERNAL_ENABLED=0\n")

    def local_service(request, timeout):
        assert request.full_url == module.ENDPOINT + "/" + issued["token"]
        payload = json.loads(request.data)
        result = store.ingest(issued["token"], "agent@example.test", payload)
        return _Response(json.dumps(result).encode())

    monkeypatch.setattr(module, "urlopen", local_service)
    args = (db_path, token_file, issued["grant_id"], "beaaa-38114-stage-b-v1-a3c8580a",
            original_ids, config)
    first = module.run(*args)
    assert first["result_state"] == "received"
    assert first["one_new_report"] is True
    assert first["original_rows_unchanged"] is True
    assert first["evidence_origin"] == "direct_http_fixture"

    retry = module.run(*args)
    assert retry["result_state"] == "duplicate"
    assert retry["one_new_report"] is False
    assert retry["report_count_after"] == first["report_count_after"]
    assert retry["original_rows_unchanged"] is True
    assert retry["schedule_count_after"] == first["schedule_count_after"]

    # A failure after the service commits must not make the operator retry insert
    # a second report. The same stable run key resolves to the committed row.
    post_write_calls = 0
    def fail_after_first_write(request, timeout):
        nonlocal post_write_calls
        post_write_calls += 1
        payload = json.loads(request.data)
        result = store.ingest(issued["token"], "agent@example.test", payload)
        if post_write_calls == 1:
            result = {**result, "state": "unusable-test-response"}
        return _Response(json.dumps(result).encode())

    monkeypatch.setattr(module, "urlopen", fail_after_first_write)
    failure_args = (db_path, token_file, issued["grant_id"],
                    "beaaa-38114-post-write-retry-a3c8580a", original_ids, config)
    with pytest.raises(RuntimeError, match="report_acceptance_failed"):
        module.run(*failure_args)
    count_after_failed_assertion = store.connection.execute(
        "SELECT count(*) FROM instinct_standing_reports").fetchone()[0]
    recovered = module.run(*failure_args)
    assert recovered["result_state"] == "duplicate"
    assert recovered["one_new_report"] is False
    assert store.connection.execute(
        "SELECT count(*) FROM instinct_standing_reports").fetchone()[0] == count_after_failed_assertion
    store.close()


def test_rejects_wrong_selector_and_non_private_token_file(tmp_path):
    module = _module()
    token_file = tmp_path / "grant.token"
    token_file.write_text("protected_existing_token_123")
    token_file.chmod(0o644)
    try:
        module._read_protected_token(token_file)
    except RuntimeError as exc:
        assert str(exc) == "grant_token_file_must_be_private"
    else:
        raise AssertionError("non-private token file accepted")


def test_installer_places_root_only_acceptance_helper():
    installer = (ROOT / "ops/stage-b-inbound/install.sh").read_text()
    assert 'accept_helper=/usr/local/sbin/backbond-stage-b-accept-v1-report' in installer
    assert 'install -o root -g root -m 0755' in installer
