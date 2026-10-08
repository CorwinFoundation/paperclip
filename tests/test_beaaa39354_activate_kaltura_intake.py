import importlib.util
from pathlib import Path
import sqlite3

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "activate", ROOT / "backbond/scan-intake/activate_kaltura_browser_transport.py"
)
activate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(activate)


def test_patch_is_surgical_and_idempotent():
    source = (ROOT / "backbond/scan-intake/server/app.py").read_text()
    legacy = source.replace(activate.NEW_TRANSPORT, activate.OLD_TRANSPORT).replace(activate.VALIDATION, "")
    custom = legacy.replace("class ScanRequest", "# locally preserved recovery/mail guards\nclass ScanRequest")
    patched = activate.patch_app(custom)
    assert patched == activate.patch_app(patched)
    assert "# locally preserved recovery/mail guards" in patched
    assert 'self.endpoint_url != "https://learning.kaltura.com/"' in patched
    assert 'self.auth.type != "none"' in patched


def test_active_or_dispatch_uncertain_scan_refuses_reload(tmp_path):
    db_path = tmp_path / "intake.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE scans (status TEXT)")
        db.execute("INSERT INTO scans VALUES ('dispatch_unknown')")
    with pytest.raises(RuntimeError, match="refusing intake reload"):
        activate.assert_idle(db_path)


def test_terminal_history_allows_reload(tmp_path):
    db_path = tmp_path / "intake.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE scans (status TEXT)")
        db.executemany("INSERT INTO scans VALUES (?)", [("finished",), ("error",), ("cancelled",)])
    activate.assert_idle(db_path)
