import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("incident", ROOT / "backbond/scan-intake/incident_reconcile_kaltura.py")
incident = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(incident)


def test_patch_is_idempotent_and_preserves_current_tenant_pack_and_report_code():
    app = (ROOT / "backbond/scan-intake/server/app.py").read_text()
    service = (ROOT / "backbond/scan-intake/server/qa_service.py").read_text()
    patched_app, patched_service = incident.patch_sources(app, service)
    assert patched_app == app and patched_service == service
    assert "terminal_failure" in patched_app
    assert '"starter-100@4"' in patched_service
    assert "report_recipient_hint" in patched_service
    assert "tenant_id" in patched_service


def test_two_exact_verified_bindings_have_distinct_reasons():
    assert incident.FAILURES == (
        ("8587f3eb7adf8e01a9eeab7a8cb69426", "3e89df46-f338-49ce-964f-23803f1d6f4b", "scanner_restart_resume_exhausted"),
        ("b8b41bbac75c9f2dd30ed71c910e65f6", "179b8cd7-76b0-4e07-81a8-7ec3d1b4ec53", "scanner_restarted_credential_not_retained"),
    )


def test_quota_patcher_preserves_legacy_upgrade_and_rejects_partial_shared_policy():
    legacy_queries = (
        '"SELECT count(*) FROM scans WHERE partner=? AND email=? AND status NOT IN (\'connection_failed\',\'cancelled\',\'interrupted\')",',
        '"SELECT count(DISTINCT email) FROM scans WHERE code=? AND status NOT IN (\'connection_failed\',\'cancelled\',\'interrupted\')",',
        '"SELECT 1 FROM scans WHERE code=? AND email=? AND status NOT IN (\'connection_failed\',\'cancelled\',\'interrupted\') LIMIT 1"',
    )
    legacy = "\n".join(legacy_queries)
    upgraded = incident.patch_quota_sources(legacy)
    assert upgraded.count("AND NOT (status='finished' AND outcome='failed')") == 3
    assert incident.patch_quota_sources(upgraded) == upgraded

    service = (ROOT / "backbond/scan-intake/server/qa_service.py").read_text()
    partial = service.replace('quota_counted_sql() + " LIMIT 1"', '"1=1 LIMIT 1"')
    with pytest.raises(RuntimeError, match="shared quota"):
        incident.patch_quota_sources(partial)
    with pytest.raises(RuntimeError, match="email quota"):
        incident.patch_quota_sources("unknown source")
