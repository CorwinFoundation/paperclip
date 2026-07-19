import importlib.util
import sqlite3
from pathlib import Path


MODULE = Path(__file__).parents[3] / "ops" / "log-tracker-backup" / "tracker_backup.py"
spec = importlib.util.spec_from_file_location("tracker_backup", MODULE)
tracker_backup = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(tracker_backup)


def test_backup_and_non_destructive_restore(tmp_path: Path) -> None:
    live = tmp_path / "tracker.sqlite3"
    with sqlite3.connect(live) as db:
        db.execute("CREATE TABLE scanner_runs (scan_id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO scanner_runs VALUES ('scan-1')")
    root = tmp_path / "backups"

    assert tracker_backup.backup(live, root) == 0
    assert tracker_backup.restore_test(root) == 0
    with sqlite3.connect(live) as db:
        assert db.execute("SELECT count(*) FROM scanner_runs").fetchone()[0] == 1


def test_restore_test_rejects_tampered_snapshot(tmp_path: Path) -> None:
    live = tmp_path / "tracker.sqlite3"
    with sqlite3.connect(live) as db:
        db.execute("CREATE TABLE scanner_runs (scan_id TEXT PRIMARY KEY)")
    root = tmp_path / "backups"
    assert tracker_backup.backup(live, root) == 0
    snapshot = next((root / "hourly").glob("*.gz"))
    snapshot.write_bytes(snapshot.read_bytes() + b"tamper")
    assert tracker_backup.restore_test(root) == 1
