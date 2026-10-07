"""BEAAA-38867: status.py prints row_count and safe refs for a real (sealed) v2 history intake.

Fictional data only.
"""
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from stage_b_pilot.history_import import HistoryImportStore
from tests.test_beaaa37907_instinct_history import KEY, SECRET, message, report as history_report

SCRIPT = Path(__file__).parents[1] / "ops/stage-b-inbound/status.py"
UNSAFE = "unsafe task/id with spaces"


def test_status_reads_row_count_from_real_v2_intake(tmp_path):
    store = HistoryImportStore(tmp_path / "history.sqlite3", provider_secret=SECRET, encryption_key=KEY)
    issued = store.issue_address("user-a", scope={"lookback_days": 90}, synthetic=True)
    rpt = history_report(3)
    rpt["rows"][0]["task_id"] = "BB-SYN-001"
    rpt["rows"][2]["task_id"] = UNSAFE
    store.ingest(message(issued.address, rpt), expected_user_id="user-a")
    sealed = store.connection.execute("SELECT report_json FROM history_previews").fetchone()[0]
    assert b"BB-SYN-001" not in bytes(sealed if isinstance(sealed, bytes) else sealed.encode())
    store.connection.close()

    now = datetime.now(timezone.utc)
    window = [(now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"), (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")]
    env = dict(os.environ, BACKBOND_STAGE_B_STATE_DIR=str(tmp_path),
               BACKBOND_STAGE_B_NGINX_LOGS=str(tmp_path / "none.log"), BACKBOND_STAGE_B_JOURNAL_CMD="true")
    out = subprocess.run([sys.executable, str(SCRIPT), *window], env=env,
                         capture_output=True, text=True, check=True).stdout
    line = next(x for x in out.splitlines() if x.startswith("  preview "))
    assert "state received row_count 3 rows 3 " in line, line
    assert "BB-SYN-001" in line
    assert UNSAFE not in out and "spaces" not in out
    assert line.count("h:") == 1, line
