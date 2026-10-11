import importlib.util
import json
import os
import sqlite3
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]
HELPER = ROOT / "backbond/scan-intake/qa-intake-counts.py"
INSTALLER = ROOT / "backbond/scan-intake/install-qa-intake-counts.sh"
SPEC = importlib.util.spec_from_file_location("qa_intake_counts", HELPER)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def _db(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE codes(code TEXT PRIMARY KEY, max_emails INTEGER, scans_per_email INTEGER);
        CREATE TABLE scans(code TEXT, email TEXT, status TEXT, outcome TEXT,
                           credential_secret TEXT, tests_json TEXT);
        INSERT INTO codes VALUES('QA-CODE', 2, 2);
        INSERT INTO scans VALUES('QA-CODE','first@example.test','reserved',NULL,
          'secret-one','[{"prompt":"private prompt"}]');
        INSERT INTO scans VALUES('QA-CODE','first@example.test','connection_failed',NULL,
          'secret-two','[{"reply":"private reply"}]');
        INSERT INTO scans VALUES('QA-CODE','second@example.test','finished','complete',
          'secret-three','[]');
        INSERT INTO scans VALUES('QA-CODE','second@example.test','finished','failed',
          'secret-four','[]');
        """)


def test_collect_returns_only_code_scoped_aggregate_evidence(tmp_path):
    path = tmp_path / "intake.sqlite"
    _db(path)
    result = MODULE.collect(path, "QA-CODE")
    encoded = json.dumps(result)

    assert result["scan_counts"] == {
        "submitted": 4, "admitted": 2, "reserved": 1,
        "connection_failed": 1, "finished": 2, "terminal": 3,
        "active_concurrency": 1,
    }
    assert result["email_identity_count"] == 2
    assert sorted(x["admitted"] for x in result["email_counts"]) == [1, 1]
    for forbidden in ("first@example.test", "second@example.test", "secret-one",
                      "private prompt", "private reply"):
        assert forbidden not in encoded


def test_rejects_query_syntax_and_unknown_codes(tmp_path):
    path = tmp_path / "intake.sqlite"
    _db(path)
    for code in ("QA-CODE' OR 1=1 --", "/etc/passwd", "QA CODE"):
        try:
            MODULE.collect(path, code)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe code accepted: {code}")
    try:
        MODULE.collect(path, "UNKNOWN")
    except LookupError:
        pass
    else:
        raise AssertionError("unknown code accepted")


def test_installer_is_idempotent_and_has_paired_uninstall(tmp_path):
    env = {**os.environ, "BACKBOND_INSTALL_ROOT": str(tmp_path)}
    for _ in range(2):
        subprocess.run([INSTALLER, "install"], check=True, env=env)
    helper = tmp_path / "usr/local/sbin/qa-intake-counts"
    sudoers = tmp_path / "etc/sudoers.d/backbond-qa-intake-counts"
    assert helper.stat().st_mode & 0o777 == 0o755
    assert sudoers.stat().st_mode & 0o777 == 0o440
    assert sudoers.read_text() == (
        "beai-agent ALL=(root) NOPASSWD: /usr/local/sbin/qa-intake-counts *\n"
    )
    subprocess.run([INSTALLER, "uninstall"], check=True, env=env)
    subprocess.run([INSTALLER, "uninstall"], check=True, env=env)
    assert not helper.exists() and not sudoers.exists()
