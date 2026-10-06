"""BEAAA-38775: Stage B helper detail + request log print no email, token or body; runtime hash is path-independent."""
import io
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "ops/stage-b-inbound/status.py"

EMAIL = "tester.person@example.com"
TOKEN = "tokSECRETreportpath0123456789abcdef"
QUERY = "token=tokSECRETquery987"
BODY_MARK = "REPORT-BODY-SENTINEL-do-not-print"
SECRETS = (EMAIL, TOKEN, "tokSECRETquery987", BODY_MARK, "203.0.113.9", "Mozilla-Sentinel", "example.com")
IN = "2026-10-06T18:45:00"
OLD = "2026-09-30T09:00:00"


def _build(state: Path) -> None:
    db = sqlite3.connect(state / "instinct-standing.sqlite3")
    db.execute("create table instinct_standing_reports (report_id, grant_digest, deduplication_key,"
               " received_at, report_json, provenance)")
    db.execute("insert into instinct_standing_reports values (?,?,?,?,?,?)",
               ("r1", TOKEN, TOKEN, OLD, BODY_MARK, BODY_MARK))
    db.execute("create table instinct_followup_requests (request_id, grant_digest, question, created_at,"
               " expires_at, replied_at, reply_json)")
    db.execute("insert into instinct_followup_requests values (?,?,?,?,?,?,?)",
               ("f1", TOKEN, BODY_MARK, IN, "2026-10-07T18:45:00", None, None))
    db.commit()
    db.close()
    db = sqlite3.connect(state / "history.sqlite3")
    db.execute("create table history_requests (token_digest, user_id, import_id, expires_at, consumed_at,"
               " state, failure_reason, consented_at, purpose)")
    db.execute("insert into history_requests values (?,?,?,?,?,?,?,?,?)",
               (TOKEN, EMAIL, "imp1", "2026-10-06T19:45:00", None, "pending", None, IN, "onboarding"))
    db.execute("create table history_events (event_id, import_id, user_id, event_type, occurred_at)")
    db.execute("insert into history_events values (?,?,?,?,?)", ("e1", "imp1", EMAIL, "request_created", IN))
    db.execute("insert into history_events values (?,?,?,?,?)", ("e2", "imp1", EMAIL, BODY_MARK, IN))
    db.commit()
    db.close()
    db = sqlite3.connect(state / "inbound.sqlite3")
    db.execute("create table inbound_receipts (receipt_id, sender_address, subject, kind, received_at,"
               " body_text, mode)")
    db.execute("insert into inbound_receipts values (?,?,?,?,?,?,?)",
               ("rc1", EMAIL, BODY_MARK, "check", IN, BODY_MARK, "DEMO"))
    db.commit()
    db.close()


def _logs(tmp: Path) -> dict:
    log = tmp / "access.log"
    log.write_text(
        f'203.0.113.9 - - [06/Oct/2026:18:50:01 +0000] "POST /stage-b-demo/instinct/report/{TOKEN} HTTP/1.1"'
        f' 403 41 "-" "Mozilla-Sentinel"\n'
        f'203.0.113.9 - - [06/Oct/2026:21:50:02 +0300] "GET /stage-b-demo/history?{QUERY} HTTP/1.1"'
        f' 200 900 "-" "Mozilla-Sentinel"\n'
        f'203.0.113.9 - - [06/Oct/2026:17:00:00 +0000] "GET /stage-b-demo/healthz HTTP/1.1" 200 2 "-" "-"\n'
        f'203.0.113.9 - - [06/Oct/2026:18:51:00 +0000] "GET /scanner/v1/sla HTTP/1.1" 200 2 "-" "-"\n')
    journal = tmp / "journalctl"
    journal.write_text("#!/bin/sh\n"
                       "echo '2026-10-06T18:50:01+0000 srv python3[1]: stage_b_reject method=POST "
                       f"route=/instinct/report/{TOKEN} status=403 reason=sender_mismatch'\n"
                       "echo '2026-10-06T18:52:00+0000 srv python3[1]: unrelated line'\n")
    journal.chmod(0o755)
    return {"BACKBOND_STAGE_B_NGINX_LOGS": str(log), "BACKBOND_STAGE_B_JOURNAL_CMD": str(journal)}


def test_detail_and_request_log_print_no_secret(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    _build(state)
    env = dict(os.environ, BACKBOND_STAGE_B_STATE_DIR=str(state), **_logs(tmp_path))
    result = subprocess.run([sys.executable, str(SCRIPT), "2026-10-06T18:30", "2026-10-06T19:30"],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    out = result.stdout + result.stderr
    for secret in SECRETS:
        assert secret not in out, secret
    assert "@" not in out
    assert f"history_event request_created import" in out and f"occurred {IN}" in out
    assert "state pending" in out and f"consented {IN}" in out
    assert "followup" in out and "expires 2026-10-07T18:45:00" in out and "replied None" in out
    # Standing reports are listed for all time, not only the window.
    assert "instinct_standing_reports total 1 in_window 0" in out and f"received {OLD}" in out
    assert "receipt" in out and "kind check" in out
    assert "req 2026-10-06T18:50:01 POST /stage-b-demo/instinct/report/h:" in out and " 403 41" in out
    assert "req 2026-10-06T18:50:02 GET /stage-b-demo/history?h:" in out
    assert "requests in_window 2" in out
    assert "reject 2026-10-06T18:50:01 POST /instinct/report/h:" in out and "403 sender_mismatch" in out
    assert "rejects in_window 1 journal_rc 0" in out


def test_receiver_reject_log_hashes_token_segments():
    sys.path.insert(0, str(ROOT))
    from stage_b_pilot.mailgun import _log_rejection

    err = io.StringIO()
    with redirect_stderr(err):
        _log_rejection("POST", f"/instinct/report/{TOKEN}?{QUERY}", 403, "sender_mismatch")
        _log_rejection("POST", "/mailgun/mime", 406, f"bad {EMAIL}")
    text = err.getvalue()
    assert TOKEN not in text and "tokSECRETquery987" not in text and EMAIL not in text
    assert "route=/instinct/report/h:" in text and "status=403 reason=sender_mismatch" in text
    assert "route=/mailgun/mime status=406 reason=unclassified" in text


def test_runtime_hash_is_path_independent(tmp_path):
    text = (ROOT / "ops/stage-b-inbound/install.sh").read_text()
    line = next(l for l in text.splitlines() if l.startswith("runtime_version="))
    assert 'cd "$repo_root" && find stage_b_pilot -name __pycache__ -prune' in line
    assert "are-deploy ALL=(backbond-stage-b : adm) NOPASSWD: %s" in text
    pipeline = line[len("runtime_version=$("):-1]
    digests = []
    for name in ("a", "deeper/b"):
        root = tmp_path / name
        shutil.copytree(ROOT / "stage_b_pilot", root / "stage_b_pilot",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (root / "stage_b_pilot/__pycache__").mkdir()
        (root / "stage_b_pilot/__pycache__/x.pyc").write_bytes(name.encode())
        out = subprocess.run(["sh", "-c", pipeline], capture_output=True, text=True,
                             env=dict(os.environ, repo_root=str(root)), check=True)
        digests.append(out.stdout.strip())
    assert len(digests[0]) == 64 and digests[0] == digests[1]
