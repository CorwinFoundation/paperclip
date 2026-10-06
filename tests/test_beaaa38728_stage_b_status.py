"""BEAAA-38728: the Stage B status helper prints counts, hashes and timestamps only."""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "ops/stage-b-inbound/status.py"

EMAIL = "tester.person@example.com"
SENDER = "agent-sender@instinct.example"
TOKEN = "tok_SECRETsessiontoken0123456789"
PASSPHRASE = "correct-horse-battery-staple"
BODY_MARK = "REPORT-BODY-SENTINEL-do-not-print"
REPORT_ID = "rep_0123456789abcdefSECRET"
SECRETS = (EMAIL, SENDER, TOKEN, PASSPHRASE, BODY_MARK, REPORT_ID, "example.com", "instinct.example")
IN = "2026-10-06T18:45:00"
OUT = "2026-10-01T09:00:00"


def _build(state: Path) -> None:
    db = sqlite3.connect(state / "onboarding.sqlite3")
    db.execute("create table onboarding_sessions (token_digest, account_email, sender_email, grant_id,"
               " passphrase, created_at, claimed_at, revoked_at)")
    db.execute("insert into onboarding_sessions values (?,?,?,?,?,?,?,?)",
               (TOKEN, EMAIL, SENDER, "grant-1", PASSPHRASE, IN, IN, None))
    db.execute("insert into onboarding_sessions values (?,?,?,?,?,?,?,?)",
               ("other", EMAIL, SENDER, "grant-2", PASSPHRASE, OUT, None, None))
    db.commit()
    db.close()
    db = sqlite3.connect(state / "instinct-standing.sqlite3")
    db.execute("create table instinct_standing_grants (grant_digest, user_id, sender_email, created_at,"
               " revoked_at, expires_at)")
    db.execute("insert into instinct_standing_grants values (?,?,?,?,?,?)",
               (TOKEN, EMAIL, SENDER, IN, None, "2026-11-06T18:45:00"))
    db.execute("create table instinct_standing_reports (report_id, grant_digest, deduplication_key,"
               " report_json, provenance, received_at)")
    body = json.dumps({"case_id": BODY_MARK, "outcome": BODY_MARK, "contact": EMAIL})
    db.execute("insert into instinct_standing_reports values (?,?,?,?,?,?)",
               (REPORT_ID, TOKEN, TOKEN, body, BODY_MARK, IN))
    db.execute("create table instinct_followup_requests (id, sender_email, body, created_at)")
    db.execute("insert into instinct_followup_requests values (1,?,?,?)", (SENDER, BODY_MARK, IN))
    db.commit()
    db.close()
    db = sqlite3.connect(state / "history.sqlite3")
    db.execute("create table history_requests (id, account_email, state, created_at, expires_at)")
    db.execute("insert into history_requests values (1,?,?,?,?)", (EMAIL, BODY_MARK, IN, IN))
    db.execute("create table history_previews (id, report_json, created_at)")
    db.execute("insert into history_previews values (1,?,?)", (json.dumps({"rows": [{"reference": BODY_MARK}]}), IN))
    db.execute("create table history_events (id, raw_encrypted, event_type, occurred_at)")
    db.execute("insert into history_events values (1,?,?,?)", (PASSPHRASE, BODY_MARK, OUT))
    db.commit()
    db.close()
    db = sqlite3.connect(state / "inbound.sqlite3")
    db.execute("create table inbound_receipts (id, sender_email, subject, body, received_at)")
    db.execute("insert into inbound_receipts values (1,?,?,?,?)", (SENDER, BODY_MARK, BODY_MARK, IN))
    db.commit()
    db.close()


def _run(state: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, BACKBOND_STAGE_B_STATE_DIR=str(state))
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env)


def test_status_prints_counts_and_no_secret_values(tmp_path):
    _build(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    result = _run(tmp_path, "2026-10-06T18:30", "2026-10-06T19:30")
    assert result.returncode == 0, result.stderr
    out = result.stdout + result.stderr
    for secret in SECRETS:
        assert secret not in out
    assert "@" not in out
    assert "onboarding_sessions total 2 in_window 1" in out
    assert "instinct_standing_grants total 1 in_window 1" in out
    assert "instinct_standing_reports total 1 in_window 1" in out
    assert "history_requests total 1 in_window 1" in out
    assert "history_previews total 1 in_window 1" in out
    assert "history_events total 1 in_window 0" in out
    assert "instinct_followup_requests total 1 in_window 1" in out
    assert "inbound_receipts total 1 in_window 1" in out
    assert IN in out
    # Read-only: the fixture files are byte-identical after the run.
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_status_rejects_malformed_window(tmp_path):
    result = _run(tmp_path, "2026-10-06'; drop table x", "2026-10-07")
    assert result.returncode == 2
    assert _run(tmp_path, "2026-10-06").returncode == 2


def test_installer_grants_are_deploy_exactly_the_status_helper():
    text = (ROOT / "ops/stage-b-inbound/install.sh").read_text()
    assert "status_helper=/opt/backbond-stage-b/bin/status.py" in text
    assert 'install -o root -g root -m 0755 "$repo_root/ops/stage-b-inbound/status.py" "$status_helper"' in text
    assert "are-deploy ALL=(backbond-stage-b) NOPASSWD: %s" in text
    assert 'visudo -cf "$status_sudoers_tmp"' in text
    assert "-m 0440" in text
