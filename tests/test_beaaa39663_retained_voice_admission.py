import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
SERVER = ROOT / "backbond/scan-intake/server"
sys.path.insert(0, str(SERVER))
SPEC = importlib.util.spec_from_file_location("admit", ROOT / "backbond/scan-intake/admit_retained_voice.py")
admit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(admit)
from qa_service import Code, IntakeStore, scanner_payload


def test_payload_defaults_public_pack_but_helper_selects_voice_total():
    base = {"email": "owner@example.test", "agent_label": "Kaltura", "endpoint_url": admit.TARGET,
            "transport": admit.TRANSPORT, "auth": {"type": "none"}, "partner": "kaltura"}
    public = scanner_payload(base, callback_url="https://api.test/cb", callback_secret_ref="ref")
    voice = scanner_payload(dict(base, pack_id=admit.PACK, expected_total=60), callback_url="https://api.test/cb", callback_secret_ref="ref")
    assert public["pack_id"] == "starter-100@4"
    assert voice["pack_id"] == "starter-voice-50@1"
    assert voice["expected_total"] == 60


def test_helper_is_idempotent_and_prints_no_private_values(tmp_path, monkeypatch, capsys):
    db_path, receipt = tmp_path / "intake.sqlite", tmp_path / "receipt.json"
    store = IntakeStore(str(db_path))
    store.put_code(Code("K", "kaltura", "Kaltura", "standard", 5, 2, "2099-01-01"))
    source = store.reserve(code="K", email="private@example.test", agent_label="Private Agent",
                           endpoint_url=admit.TARGET, transport=admit.TRANSPORT,
                           terms_version="v1", improvement_consent=True)
    store.db.execute("UPDATE scans SET status='finished',outcome='complete' WHERE id=?", (source["scan_id"],))
    monkeypatch.setattr(admit, "_post_json", lambda *a, **k: {"scan_id": "scanner-voice-1"})
    argv = ["--source-intake-id", source["scan_id"], "--pack-id", admit.PACK,
            "--expected-total", "60", "--reuse-recipient", "--reuse-consent",
            "--receipt", str(receipt), "--db", str(db_path)]
    assert admit.main(argv) == 0
    assert admit.main(argv) == 0
    output = capsys.readouterr().out
    assert "private@example.test" not in output and "Private Agent" not in output
    assert os.stat(receipt).st_mode & 0o777 == 0o600
    row = sqlite3.connect(db_path).execute("SELECT pack_version,scanner_id FROM scans WHERE source_intake_id=?", (source["scan_id"],)).fetchone()
    assert row == (admit.PACK, "scanner-voice-1")
    assert json.loads(receipt.read_text())["expected_total"] == 60


def test_helper_has_no_third_party_runtime_dependency():
    source = (ROOT / "backbond/scan-intake/admit_retained_voice.py").read_text()
    assert "import httpx" not in source
    assert "from urllib.request import Request, urlopen" in source


def test_duplicate_check_supports_legacy_schema_without_source_binding():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE scans(id TEXT, endpoint_host TEXT, pack_version TEXT)")
    db.execute("INSERT INTO scans VALUES('old', 'learning.kaltura.com', ?)", (admit.PACK,))
    store = type("LegacyStore", (), {"db": db})()

    assert not admit._supports_source_binding(store)
    assert admit._duplicate_admission(store, "source", "learning.kaltura.com")[0] == "old"


def test_duplicate_check_uses_source_binding_when_available():
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE scans(id TEXT, endpoint_host TEXT, pack_version TEXT, source_intake_id TEXT)"
    )
    db.execute("INSERT INTO scans VALUES('old', 'other.example', 'other-pack', 'source')")
    store = type("CurrentStore", (), {"db": db})()

    assert admit._supports_source_binding(store)
    assert admit._duplicate_admission(store, "source", "learning.kaltura.com")[0] == "old"
