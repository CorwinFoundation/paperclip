"""P04: the API export is an owner-scoped view of the durable result."""

import pytest

from api.accepted_scan_store import AcceptedScanStore
from api.handlers import HandlerError
from api.scan_handlers import ScanRuntime, scan_operations_export_handler
from scanners.live_probe_orchestrator import ScanRequest, ScanResult


def _runtime(tmp_path):
    store = AcceptedScanStore(tmp_path / "accepted.sqlite3")
    runtime = ScanRuntime(
        run_scan_fn=lambda request, secret: ScanResult(
            scan_id=request.scan_id,
            status="complete",
            scanner_version=request.scanner_version,
            corpus_version=request.corpus_version,
            rows_emitted=1,
            full_suite_manifest={
                "manifest_hash": "sha256:manifest",
                "evidence_rows": [{"evidence_ref": "evidence://case-a"}],
            },
        ),
        accepted_scan_store=store,
    )
    request = ScanRequest(
        scan_id="scan-1",
        tenant_id="tenant-a",
        agent_id="agent-1",
        agent_endpoint="https://agent.example/v1",
        credential_ref="none",
        endpoint_config={"url": "https://agent.example/v1", "auth": {"type": "none"}},
        corpus_snapshot_hash="sha256:" + "a" * 64,
        scanner_version="0.5.0",
        corpus_version="0.2.0",
        seed=0,
    )
    scope = {
        "registry_revision": "registry@1",
        "mode": "full",
        "case_manifest": ["case-a"],
        "case_revisions": {"case-a": "case-a@7"},
        "complete": True,
        "held": False,
    }
    store.reserve_request_identity(
        tenant_id="tenant-a",
        client_request_id="request-1",
        canonical_request_hash="sha256:" + "b" * 64,
        candidate_scan_id="scan-1",
        pins={
            "target": {"agent_id": "agent-1", "endpoint": "https://agent.example/v1"},
            "release": {"scanner_version": "0.5.0", "corpus_version": "0.2.0"},
            "scope": {"operations_scope": scope},
        },
    )
    store.accept(request, agent_id="agent-1", invocation_context=None, webhook_cfg=None)
    store.mark_terminal("scan-1", runtime._run_scan_fn(request, ""))
    return runtime


def test_export_reads_terminal_result_and_prework_pins(tmp_path):
    status, export = scan_operations_export_handler(
        "scan-1", runtime=_runtime(tmp_path), authenticated_tenant_id="tenant-a"
    )
    assert status == 200
    assert export["version"] == "scan_export_v1"
    assert export["pins"]["case_revisions"] == {"case-a": "case-a@7"}
    assert export["evidence_refs"] == ["evidence://case-a"]
    assert export["integrity_refs"] == ["sha256:manifest"]
    assert export["result_revision"] == export["result_digest"]


@pytest.mark.parametrize("tenant", [None, "tenant-b"])
def test_export_requires_auth_and_hides_foreign_identity(tmp_path, tenant):
    with pytest.raises(HandlerError) as caught:
        scan_operations_export_handler(
            "scan-1", runtime=_runtime(tmp_path), authenticated_tenant_id=tenant
        )
    assert caught.value.status == (401 if tenant is None else 404)

