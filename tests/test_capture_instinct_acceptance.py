import importlib.util
from datetime import datetime, timezone
from pathlib import Path

from stage_b_pilot.instinct_standing import StandingReportStore


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "capture_instinct_acceptance",
    ROOT / "ops/stage-b-inbound/capture-instinct-acceptance.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_capture_is_redacted_and_proves_six_case_identity(tmp_path):
    now = datetime(2026, 10, 5, 6, tzinfo=timezone.utc)
    db = tmp_path / "standing.sqlite3"
    store = StandingReportStore(db, now=lambda: now)
    issued = store.issue("pilot-user", "private@example.test", followup_enabled=True)
    store.schedule_risk_checks(
        "pilot-user", issued["grant_id"], "valid_intake", trigger_key="fresh-intake-001"
    )
    store.close()

    result = MODULE.capture(db, issued["grant_id"])

    assert result["schedule_count"] == 6
    assert result["followup_count"] == 6
    assert result["schedule_case_ids"] == [
        "IH-H1", "IH-H2", "IH-A1", "IH-A2", "IH-C1", "IH-C2"
    ]
    encoded = repr(result)
    assert issued["token"] not in encoded
    assert "private@example.test" not in encoded
    assert "Fictional record" not in encoded


def test_capture_hash_changes_when_selected_grant_changes(tmp_path):
    now = datetime(2026, 10, 5, 6, tzinfo=timezone.utc)
    db = tmp_path / "standing.sqlite3"
    store = StandingReportStore(db, now=lambda: now)
    issued = store.issue("pilot-user", "private@example.test", followup_enabled=True)
    before = MODULE.capture(db, issued["grant_id"])
    store.schedule_risk_checks(
        "pilot-user", issued["grant_id"], "valid_intake", trigger_key="fresh-intake-001"
    )
    after = MODULE.capture(db, issued["grant_id"])
    store.close()

    assert before["schedule_rows_sha256"] != after["schedule_rows_sha256"]
    assert before["followup_rows_sha256"] != after["followup_rows_sha256"]
