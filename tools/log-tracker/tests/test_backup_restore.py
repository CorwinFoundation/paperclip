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


def test_restore_test_accepts_read_only_backup_root(tmp_path: Path) -> None:
    live = tmp_path / "tracker.sqlite3"
    with sqlite3.connect(live) as db:
        db.execute("CREATE TABLE scanner_runs (scan_id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO scanner_runs VALUES ('scan-1')")
    root = tmp_path / "backups"
    assert tracker_backup.backup(live, root) == 0
    paths = [root, *root.rglob("*")]
    modes = {path: path.stat().st_mode for path in paths}
    try:
        for path in paths:
            path.chmod(0o555 if path.is_dir() else 0o444)
        assert tracker_backup.restore_test(root) == 0
    finally:
        for path in reversed(paths):
            path.chmod(modes[path])


def test_backup_reads_active_wal_from_read_only_source_directory(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    live = source_dir / "tracker.sqlite3"
    writer = sqlite3.connect(live)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("CREATE TABLE scanner_runs (scan_id TEXT PRIMARY KEY)")
        writer.commit()
        writer.execute("INSERT INTO scanner_runs VALUES ('scan-in-wal')")
        writer.commit()
        assert live.with_name(live.name + "-wal").exists()
        original_mode = source_dir.stat().st_mode
        source_dir.chmod(0o555)
        try:
            root = tmp_path / "backups"
            assert tracker_backup.backup(live, root) == 0
            assert tracker_backup.restore_test(root) == 0
            record = __import__("json").loads(
                (root / "MANIFEST.jsonl").read_text().splitlines()[-1]
            )
            assert record["row_counts"]["scanner_runs"] == 1
        finally:
            source_dir.chmod(original_mode)
    finally:
        writer.close()
