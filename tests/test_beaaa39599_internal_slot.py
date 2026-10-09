import importlib.util
import sqlite3
from pathlib import Path
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "backbond/scan-intake/server/reconcile_internal_slot.py"
spec = importlib.util.spec_from_file_location("reconcile_internal_slot", SCRIPT)
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


def database(tmp_path):
    path = tmp_path / "intake.sqlite"
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE codes(code TEXT PRIMARY KEY, partner TEXT, partner_label TEXT, kind TEXT, max_emails INTEGER, scans_per_email INTEGER, expires_at TEXT, active INTEGER);
    CREATE TABLE scans(id TEXT PRIMARY KEY, code TEXT, partner TEXT, email TEXT, agent_label TEXT, endpoint_host TEXT, transport TEXT, pack_version TEXT, created_at TEXT, status TEXT, report_token TEXT, tests_json TEXT, finished_at TEXT, scanner_id TEXT, status_token_hash TEXT, outcome TEXT, incomplete_reason TEXT);
    INSERT INTO codes VALUES('INTERNAL-VERIFY-194F729D','backbond','BackBond','standard',1,1,'2099-01-01',1);
    INSERT INTO scans(id,code,partner,email,agent_label,endpoint_host,transport,pack_version,created_at,status) VALUES('stale','INTERNAL-VERIFY-194F729D','backbond','old@backbond.ai','old','example.com','mcp','v','2026-01-01','reserved');
    """)
    db.commit(); db.close(); return path


def test_release_and_idempotence(tmp_path):
    path = database(tmp_path)
    assert module.reconcile(path, 'INTERNAL-VERIFY-194F729D', 'ari@backbond.ai') == ('stale', 0, 1)
    assert module.reconcile(path, 'INTERNAL-VERIFY-194F729D', 'ari@backbond.ai') == ('already_admissible', 0, 1)


def test_refuses_launched_or_external(tmp_path):
    path = database(tmp_path)
    db = sqlite3.connect(path); db.execute("UPDATE scans SET scanner_id='remote'"); db.commit(); db.close()
    with pytest.raises(ValueError, match='found 0'):
        module.reconcile(path, 'INTERNAL-VERIFY-194F729D', 'ari@backbond.ai')
    with pytest.raises(ValueError, match='only INTERNAL'):
        module.reconcile(path, 'INTERNAL-VERIFY-194F729D', 'ari@example.com')
