import json
from pathlib import Path

from api.scan_runtime_factory import make_default_production_scan_runtime


def _active_registry_version() -> str:
    path = Path(__file__).parents[1] / "tools/probe-registry/active_registry.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))["metadata"]
    return (
        f'{metadata["probe_set_version"]}'
        f'+source-closure-{metadata["source_closure_sha"]}'
    )


def test_production_factory_accepts_non_executing_scope_review(monkeypatch, tmp_path):
    monkeypatch.setenv("ARE_SCAN_LOG_PATH", str(tmp_path / "scan_log.sqlite3"))
    monkeypatch.setenv("ARE_PRODUCTION_CONTROL_LEDGER_DIR", str(tmp_path / "controls"))
    monkeypatch.setenv("ARE_SCORER_REAL", "off")
    monkeypatch.setenv("ARE_WALKER_CUSTOMER_AGENT", "off")
    monkeypatch.delenv("ARE_FIRED_PROBE_LEDGER_PATH", raising=False)

    runtime = make_default_production_scan_runtime()
    revision = _active_registry_version()
    assert {row["registry_version"] for row in runtime._full_scan_registry_rows} == {
        revision
    }
    assert all(
        row["scope_id"] == row["risk_or_rubric_id"]
        for row in runtime._full_scan_registry_rows
    )
    scope_id = runtime._full_scan_registry_rows[0]["scope_id"]
    case_manifest = [
        row["case_id"]
        for row in runtime._full_scan_registry_rows
        if row["scope_id"] == scope_id
    ]

    accepted = runtime.validate_operations_scope(
        {
            "registry_revision": revision,
            "mode": "scope_review",
            "scope_ids": [scope_id],
            "case_manifest": case_manifest,
        }
    )

    assert accepted["registry_revision"] == revision
    assert accepted["mode"] == "scope_review"
    assert accepted["requested_case_ids"] == sorted(case_manifest)
    assert accepted["may_claim_full_score"] is False
