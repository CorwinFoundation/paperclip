"""BEAAA-38869: scan_log reads are scoped to the caller's tenant.

``agent_id`` is caller-supplied, so two tenants (or a public user and
BackBond's own Vapi lane) can send the same one. Neither may see the other's
prior scans or rescan chain, and rows written before ``tenant_id`` existed are
tagged with the legacy tenant so no partner tenant can read them.
"""
from __future__ import annotations

import sqlite3

from api.scan_log import LEGACY_TENANT_ID, ScanLog

SCORE = {"are_total": 70, "subscores": {"GOVERNANCE": 18}}


def _append(log, tenant, ts, scan_id):
    log.append_scan(
        agent_id="shared-agent",
        run_timestamp=ts,
        scan_id=scan_id,
        scanner_version="0.3.6",
        corpus_version="1.2.0",
        score_json=SCORE,
        forensic_bundle_uri=f"https://b/{scan_id}",
        tenant_id=tenant,
    )


def test_two_tenants_sharing_agent_id_never_see_each_other():
    log = ScanLog()
    _append(log, "tenant-a", "2026-10-07T10:00:00Z", "a-1")
    _append(log, "tenant-a", "2026-10-07T11:00:00Z", "a-2")
    _append(log, "tenant-b", "2026-10-07T10:00:00Z", "b-1")

    a = [r["scan_id"] for r in log.list_for_agent("shared-agent", tenant_id="tenant-a")]
    b = [r["scan_id"] for r in log.list_for_agent("shared-agent", tenant_id="tenant-b")]
    assert a == ["a-1", "a-2"]
    assert b == ["b-1"]
    assert log.list_for_agent("shared-agent", tenant_id="tenant-c") == []

    # Same (agent_id, run_timestamp) under two tenants: each get() returns its own row.
    assert log.get("shared-agent", "2026-10-07T10:00:00Z", tenant_id="tenant-a")["scan_id"] == "a-1"
    assert log.get("shared-agent", "2026-10-07T10:00:00Z", tenant_id="tenant-b")["scan_id"] == "b-1"
    assert log.get("shared-agent", "2026-10-07T11:00:00Z", tenant_id="tenant-b") is None


def test_pre_tenant_table_is_rebuilt_and_rows_tagged_legacy(tmp_path):
    # Build the DB with master's pre-tenant scan_log module, exactly as the
    # live srv1749248 file was written, then reopen it with the new module.
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "scan_log_pre_tenant", Path(__file__).parent / "fixtures_beaaa38869_scan_log_pre_tenant.py"
    )
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    path = tmp_path / "scan_log.db"
    old_log = old.ScanLog(path)
    old_log.append_scan(
        agent_id="shared-agent",
        run_timestamp="2026-10-01T00:00:00Z",
        scan_id="old-1",
        scanner_version="0.3.6",
        corpus_version="1.2.0",
        score_json=SCORE,
        forensic_bundle_uri="https://b/old-1",
    )
    old_log.close()

    log = ScanLog(path)
    assert log.list_for_agent("shared-agent", tenant_id="tenant-a") == []
    legacy = log.list_for_agent("shared-agent", tenant_id=LEGACY_TENANT_ID)
    assert [r["scan_id"] for r in legacy] == ["old-1"]
    # Write-once trigger survives the rebuild.
    import pytest
    from api.scan_log import ScanLogError

    with pytest.raises((ScanLogError, sqlite3.IntegrityError)):
        log._conn.execute(
            "UPDATE scan_log SET forensic_bundle_uri = 'x' WHERE scan_id = 'old-1'"
        )
    log.close()
    # Reopening is a no-op (idempotent migration).
    assert [r["scan_id"] for r in ScanLog(path).list_for_agent("shared-agent", tenant_id=LEGACY_TENANT_ID)] == ["old-1"]
