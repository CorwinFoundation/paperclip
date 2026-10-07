"""BEAAA-38867 Oct 7 fixes: named field errors, example acceptance, follow-up consent copy, status row_count."""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from stage_b_pilot.instinct_standing import (
    EVENT_KEYS, SCHEMA_VERSION, StandingReportStore,
)
from stage_b_pilot.receipt_page import enrollment_instruction

ROOT = Path(__file__).parents[1]
STATUS_SCRIPT = ROOT / "ops/stage-b-inbound/status.py"
IN = "2026-10-07T07:50:00"


# ── helpers ──────────────────────────────────────────────────────────────────

def _base_report(override=None, dedup_key="fictional-fix-001"):
    event = {field: "unknown" for field in EVENT_KEYS - {"synthetic"}}
    event.update(evidence_origin="agent_reported", synthetic=True,
                 observed_channel="unknown", shared_context_status="unverified",
                 category="payment", money_at_risk="$150",
                 proposed_external_action="Transfer $150",
                 actual_external_action="unknown", approval_source="user_instruction",
                 approval_status="approved", outcome="unknown", gaps="unknown",
                 occurred_at="2024-09-15",
                 history_sources_included="unknown", history_sources_unavailable="unknown")
    if override:
        event.update(override)
    return {"schema_version": SCHEMA_VERSION, "deduplication_key": dedup_key,
            "trigger": "request_completed", "event": event}


# ── Test 1: numeric money_at_risk → named error ───────────────────────────────

def test_numeric_event_field_raises_named_error(tmp_path):
    store = StandingReportStore(tmp_path / "standing.sqlite3")
    grant = store.issue("user-a", "agent@example.com")
    bad = _base_report({"money_at_risk": 150})   # integer, not string
    with pytest.raises(ValueError, match=r"standing_event_value_invalid:money_at_risk:not_string"):
        store.ingest(grant["token"], "agent@example.com", bad)


def test_too_long_field_raises_named_error(tmp_path):
    store = StandingReportStore(tmp_path / "standing.sqlite3")
    grant = store.issue("user-a", "agent@example.com")
    bad = _base_report({"gaps": "x" * 501})
    with pytest.raises(ValueError, match=r"standing_event_value_invalid:gaps:too_long"):
        store.ingest(grant["token"], "agent@example.com", bad)


# ── Test 2: documented example report is accepted ────────────────────────────

def test_documented_example_is_accepted(tmp_path):
    store = StandingReportStore(tmp_path / "standing.sqlite3")
    grant = store.issue("user-a", "agent@example.com")
    result = store.ingest(grant["token"], "agent@example.com", _base_report())
    assert result["state"] == "received"


# ── Test 3: v2 (BEAAA-38867 Stage B v2) — the user message carries no follow-up/polling clause ──

def test_enrollment_instruction_has_no_followup_polling_clause():
    msg = enrollment_instruction("some-token-123", "https://demo.backbond.ai")
    assert "check for questions" not in msg  # v2 has no polling (CMO short text)
    assert "hourly" not in msg
    assert "Stop BackBond" in msg
    assert "If a send fails, tell me the HTTP status and error code." in msg


# ── Test 4: status.py row_count output ───────────────────────────────────────

def _build_history_state(state: Path) -> None:
    db = sqlite3.connect(state / "history.sqlite3")
    report_json = json.dumps({
        "schema_version": "instinct-history/v1", "row_count": 3,
        "rows": [{"deduplication_key": "BB-LIVE-CONT-001"},
                 {"deduplication_key": "BB-LIVE-CONT-002"},
                 {"deduplication_key": "some-opaque-id-789"}],
        "end_marker": "true",
    })
    db.execute("CREATE TABLE history_previews "
               "(preview_id TEXT, user_id TEXT, token_digest TEXT, report_json TEXT, "
               "raw_encrypted BLOB, provenance TEXT, state TEXT, created_at TEXT, "
               "confirmed_at TEXT, retention_expires_at TEXT)")
    db.execute("INSERT INTO history_previews VALUES (?,?,?,?,?,?,?,?,?,?)",
               ("prev-1", "user-x", "tok-x", report_json, b"", "agent_reported",
                "received", IN, None, ""))
    db.commit(); db.close()


def _run_status(state: Path, since: str, until: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, BACKBOND_STAGE_B_STATE_DIR=str(state))
    return subprocess.run([sys.executable, str(STATUS_SCRIPT), since, until],
                         capture_output=True, text=True, env=env)


def test_status_history_previews_row_count(tmp_path):
    _build_history_state(tmp_path)
    result = _run_status(tmp_path, "2026-10-07T00:00:00", "2026-10-07T23:59:59")
    assert result.returncode == 0, result.stderr
    out = result.stdout + result.stderr
    assert "row_count 3" in out, f"row_count missing. Output: {out[:800]}"
    # Legacy clear-JSON fallback: counts only; refs come from synthetic task_ids
    # (real sealed v2 intake: tests/test_beaaa38867_status_row_count_v2.py).
    # opaque ID should be hashed (not printed verbatim)
    assert "some-opaque-id-789" not in out, f"opaque ID printed verbatim. Output: {out[:800]}"
    assert "rows 3" in out, f"rows count missing. Output: {out[:800]}"
