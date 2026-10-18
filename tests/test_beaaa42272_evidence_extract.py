"""BEAAA-42272: sanitized scan-id lookup through scanner-deploy."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path


HELPER = Path(__file__).resolve().parents[1] / "scripts/deploy/scanner-deploy.sh"
SCAN_ID = "f530b033-46d2-4c37-a2a2-b8cad01824fa"
JOB_ID = "a935915a-ddc0-473f-ba46-ebd3593cc38b"


def _database(path: Path, *, historical: bool = False) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE full_scan_jobs (job_id TEXT, job_json TEXT)")
    conn.execute("CREATE TABLE full_scan_idempotency (idempotency_key TEXT, job_id TEXT)")
    record = {
        "job_id": JOB_ID,
        "status": "incomplete",
        "scanner_version": "0.5.0",
        "corpus_version": "registry-2026-10-18",
        "tenant_id": "must-not-leak",
        "agent_id": "must-not-leak",
        "terminal_webhook": {"secret_ref": "must-not-leak"},
        "registry_cases": [
            {"case_id": "case-a", "required": True},
            {"case_id": "case-b", "required": True},
            {"case_id": "case-c", "required": False},
        ],
        "evidence_rows": [
            {"probe_id": "case-a", "status": "complete", "evidence_ref": "evidence://private/a"},
            {"probe_id": "case-b", "status": "incomplete", "incomplete_reason": "timeout"},
        ],
        "bind_eligibility": "blocked_base_suite_incomplete",
        "final_manifest_hash": "sha256:" + "a" * 64,
        "final_manifest": {
            "registry_snapshot_hash": "sha256:" + "b" * 64,
            "reconciliation": {"attempted": 2, "terminal": 2, "evidenced": 1},
            "incomplete_reasons": ["case-b:timeout"],
            "bind_eligibility": "blocked_base_suite_incomplete",
        },
    }
    if not historical:
        record["scan_id"] = SCAN_ID
    conn.execute("INSERT INTO full_scan_jobs VALUES (?, ?)", (JOB_ID, json.dumps(record)))
    if historical:
        conn.execute(
            "INSERT INTO full_scan_idempotency VALUES (?, ?)",
            (f"private-tenant:{SCAN_ID}:full-registry", JOB_ID),
        )
    conn.commit()
    conn.close()


def _accepted_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE accepted_scans (scan_id TEXT PRIMARY KEY, tenant_id TEXT)")
    conn.execute("INSERT INTO accepted_scans VALUES (?, ?)", (SCAN_ID, "private-tenant"))
    conn.commit()
    conn.close()


def _run(state: Path, db: Path, scan_id: str = SCAN_ID) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(HELPER), "evidence-extract", scan_id],
        env={**os.environ, "SCANNER_EVIDENCE_STATE_DIR": str(state),
             "SCANNER_FULL_SCAN_JOB_STORE": str(db),
             "SCANNER_ACCEPTED_SCAN_STORE": str(state / "accepted_scans.sqlite3")},
        text=True, capture_output=True, check=False,
    )


def test_extract_returns_only_sanitized_expected_vs_actual_projection(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    db = state / "full_scan_jobs.sqlite3"
    _database(db)

    result = _run(state, db)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["job_id"] == JOB_ID
    assert output["registry"]["snapshot_hash"] == "sha256:" + "b" * 64
    assert output["expected_required"] == {"count": 2, "case_ids": ["case-a", "case-b"]}
    assert output["attempted"] == {"count": 2, "case_ids": ["case-a", "case-b"]}
    assert output["terminal"] == {"count": 2, "case_ids": ["case-a", "case-b"]}
    assert output["evidenced"] == {"count": 1, "case_ids": ["case-a"]}
    assert output["incomplete_rows"] == [
        {"case_id": "case-b", "status": "incomplete", "incomplete_reason": "timeout"}
    ]
    assert output["final_manifest_hash"] == "sha256:" + "a" * 64
    for forbidden in ("must-not-leak", "evidence://private/a", "tenant_id", "agent_id", "secret_ref"):
        assert forbidden not in result.stdout


def test_extract_is_idempotent_and_does_not_write_database(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    db = state / "full_scan_jobs.sqlite3"
    _database(db)
    before = db.read_bytes()
    first = _run(state, db)
    second = _run(state, db)
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    assert db.read_bytes() == before


def test_extract_correlates_historical_job_through_accepted_scan(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    db = state / "full_scan_jobs.sqlite3"
    accepted_db = state / "accepted_scans.sqlite3"
    _database(db, historical=True)
    _accepted_database(accepted_db)
    before = (db.read_bytes(), accepted_db.read_bytes())

    result = _run(state, db)

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["scan_id"] == SCAN_ID
    assert output["job_id"] == JOB_ID
    assert "private-tenant" not in result.stdout
    assert (db.read_bytes(), accepted_db.read_bytes()) == before


def test_extract_rejects_bad_or_unknown_scan_id(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    db = state / "full_scan_jobs.sqlite3"
    _database(db)
    bad = _run(state, db, "../../etc/shadow")
    assert bad.returncode != 0
    assert "scan id must be a lowercase uuid" in bad.stderr
    missing = _run(state, db, "00000000-0000-0000-0000-000000000000")
    assert missing.returncode != 0
    assert "scan id not found" in missing.stderr


def test_extract_refuses_symlinked_store(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    real = tmp_path / "full_scan_jobs.sqlite3"
    _database(real)
    link = state / "full_scan_jobs.sqlite3"
    link.symlink_to(real)
    result = _run(state, link)
    assert result.returncode != 0
    assert "job store is a symlink" in result.stderr
