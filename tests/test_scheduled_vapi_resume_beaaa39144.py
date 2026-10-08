"""BEAAA-39144: scheduled Vapi scans retain per-case progress."""

from pathlib import Path


def test_vapi_run_uses_durable_checkpoint_store_and_stable_key() -> None:
    source = Path("scripts/scheduled_scan.py").read_text(encoding="utf-8")

    assert 'FullScanJobPersistence(output_dir / "full_scan_jobs.sqlite3")' in source
    assert 'f"scheduled-{job_name}:{run_date}:"' in source
    assert "resumed = store.resume(job.job_id)" in source
    assert "registry_rows=pending_rows" in source
    assert "if job.active_attempt_id is not None:" in source
