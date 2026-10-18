import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.bundle import BundleStore
from api.edge_gateway import EdgeGateway, KeyStore, RateLimitConfig, RateLimiter, _cli
from rubric.scoring import Rubric


TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SCOPES = (
    "GET /scanner/v1/release-identity",
    "POST /scanner/v1/scans",
    "GET /scan/{scan_id}",
    "GET /scanner/v1/scans/{scan_id}/export",
)


def _client(tmp_path: Path, *, scopes=SCOPES):
    store = KeyStore(tmp_path / "keys.json", cache_ttl_seconds=0)
    store.issue(
        token="bac-token", key_id="bac-operations-1", tenant_id=TENANT,
        ttl_seconds=300, rate_limit={"scans_per_min": 2, "max_concurrent": 1},
        scopes=scopes, issuer="scanner-platform", custodian="platform-maintainer",
        rotation_owner="platform-maintainer", revocation_owner="scanner-operator",
        revocation_procedure="python -m api.edge_gateway revoke --key-id bac-operations-1",
    )
    app = create_app(
        rubric=Rubric.load(Path(__file__).parents[1] / "rubric" / "rubric.yaml"),
        bundles=BundleStore(), scan_runtime=None,
    )
    app.state.edge_gateway = EdgeGateway(
        store=store, limiter=RateLimiter(),
        defaults=RateLimitConfig(scans_per_min=10, max_concurrent=3),
    )
    return TestClient(app, raise_server_exceptions=False), store


def test_governed_metadata_persists_without_secret(tmp_path):
    _, store = _client(tmp_path)
    record = store.list_records()[0]
    assert record.key_id == "bac-operations-1"
    assert record.scopes == tuple(sorted(SCOPES))
    assert record.issuer == "scanner-platform"
    assert record.custodian == "platform-maintainer"
    assert record.rotation_owner == "platform-maintainer"
    assert record.revocation_owner == "scanner-operator"
    assert record.expires_at > record.created_at
    raw = (tmp_path / "keys.json").read_text()
    assert "bac-token" in raw  # existing store format; never returned by metadata APIs


def test_explicit_scopes_allow_only_named_routes(tmp_path):
    client, _ = _client(tmp_path, scopes=("POST /scanner/v1/scans",))
    headers = {"Authorization": "Bearer bac-token", "X-Tenant-Id": TENANT}
    assert client.post("/scanner/v1/scans", headers=headers, json={}).status_code != 403
    denied = client.get("/scan/example", headers=headers)
    assert denied.status_code == 403
    assert denied.json()["detail"] == "authorization_scope_denied"


def test_new_explicit_empty_scope_record_denies_all_governed_routes(tmp_path):
    client, _ = _client(tmp_path, scopes=())
    headers = {"Authorization": "Bearer bac-token", "X-Tenant-Id": TENANT}
    for method, path in (
        (client.post, "/scanner/v1/scans"),
        (client.get, "/scan/example"),
        (client.get, "/scanner/v1/scans/example/progress"),
        (client.get, "/scanner/v1/scans/example/export"),
    ):
        response = method(path, headers=headers, **({"json": {}} if method == client.post else {}))
        assert response.status_code == 403, response.text


def test_governed_key_is_denied_on_every_authenticated_route_without_named_scope(tmp_path, monkeypatch):
    client, _ = _client(tmp_path)
    headers = {"Authorization": "Bearer bac-token", "X-Tenant-Id": TENANT}
    monkeypatch.setenv("ARE_RESCANS_MEASURED", "off")
    cases = (
        ("post", "/score", {}),
        ("get", "/forensic_bundle/example", None),
        ("get", "/scanner/v1/scans/example/progress", None),
        ("get", "/internal/scanner/v1/scans/example/context-report", None),
        ("post", "/quote", {}),
        ("post", "/scanner/v1/rescans", {}),
    )
    for method, path, body in cases:
        response = getattr(client, method)(path, headers=headers, json=body) if body is not None else getattr(client, method)(path, headers=headers)
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["detail"] == "authorization_scope_denied"


def test_real_pre_scope_file_preserves_established_authenticated_access(tmp_path, monkeypatch):
    store_path = tmp_path / "keys.json"
    store_path.write_text(json.dumps({
        "version": 1,
        "keys": {
            "legacy-token": {
                "key_id": "customer-before-scopes",
                "tenant_id": TENANT,
                "status": "active",
                "expires_at": 2_000_000_000,
                "created_at": 1_700_000_000,
                "rate_limit": {"scans_per_min": 10, "max_concurrent": 3},
            }
        },
    }))
    store = KeyStore(store_path, cache_ttl_seconds=0)
    assert store.list_records()[0].scopes is None
    app = create_app(
        rubric=Rubric.load(Path(__file__).parents[1] / "rubric" / "rubric.yaml"),
        bundles=BundleStore(), scan_runtime=None,
    )
    app.state.edge_gateway = EdgeGateway(
        store=store, limiter=RateLimiter(),
        defaults=RateLimitConfig(scans_per_min=10, max_concurrent=3),
    )
    client = TestClient(app, raise_server_exceptions=False)
    headers = {"Authorization": "Bearer legacy-token", "X-Tenant-Id": TENANT}
    monkeypatch.setenv("ARE_RESCANS_MEASURED", "off")
    # These reach their handlers instead of the new 403 scope boundary.
    assert client.post("/scanner/v1/scans", headers=headers, json={}).status_code == 503
    assert client.get("/scan/example", headers=headers).status_code == 503
    assert client.get("/scanner/v1/scans/example/export", headers=headers).status_code == 503
    assert client.get("/scanner/v1/release-identity", headers=headers).status_code != 403
    assert client.post("/scanner/v1/rescans", headers=headers, json={}).status_code == 200

    # An unrelated mutation must not silently add an explicit scopes field.
    assert store.set_rate_limit("customer-before-scopes", {"scans_per_min": 5, "max_concurrent": 2})
    assert "scopes" not in json.loads(store_path.read_text())["keys"]["legacy-token"]


def test_tenant_mismatch_rejected_before_scan_runtime(tmp_path):
    client, _ = _client(tmp_path)
    response = client.post(
        "/scanner/v1/scans", json={},
        headers={"Authorization": "Bearer bac-token", "X-Tenant-Id": "other-tenant"},
    )
    assert response.status_code == 409
    assert "tenant mismatch" in response.json()["detail"]


def test_governed_progress_scope_returns_only_own_scan_progress(tmp_path):
    from api.accepted_scan_store import AcceptedScanStore
    from api.scan_handlers import ScanRuntime
    from scanners.live_probe_orchestrator import ScanRequest

    scope = "GET /scanner/v1/scans/{scan_id}/progress"
    client, keys = _client(tmp_path, scopes=(scope,))
    accepted = AcceptedScanStore(tmp_path / "accepted.sqlite3")
    request = ScanRequest(
        scan_id="own-progress", tenant_id=TENANT,
        agent_endpoint="https://agent.invalid", credential_ref="private-key-ref",
        corpus_snapshot_hash="sha256:test", scanner_version="test",
        corpus_version="test", seed=42, pack_id="starter-100@4", agent_id="agent-1",
    )
    accepted.accept(request, agent_id="agent-1", invocation_context=None, webhook_cfg=None)
    accepted.mark_running(request.scan_id)
    accepted.record_case_progress(request.scan_id, case_id="case-1", family="safety", state="finished")
    client.app.state.scan_runtime = ScanRuntime(run_scan_fn=lambda *_: None, accepted_scan_store=accepted)
    keys.issue(token="foreign-token", key_id="foreign", tenant_id="foreign",
               ttl_seconds=300, scopes=(scope,))

    own = client.get("/scanner/v1/scans/own-progress/progress", headers={"Authorization":"Bearer bac-token"})
    assert own.status_code == 200, own.text
    assert own.json()["processed_count"] == 1
    assert own.json()["accepted_pack_total"] == 100
    assert "private-key-ref" not in own.text
    assert "result_rows" not in own.json()
    foreign = client.get("/scanner/v1/scans/own-progress/progress", headers={"Authorization":"Bearer foreign-token"})
    assert foreign.status_code == 404
    assert client.get("/scanner/v1/scans/own-progress/progress").status_code == 401


def test_scan_result_read_scope_does_not_grant_progress_implicitly(tmp_path):
    client, _ = _client(tmp_path, scopes=("GET /scan/{scan_id}",))
    response = client.get("/scanner/v1/scans/example/progress", headers={"Authorization":"Bearer bac-token"})
    assert response.status_code == 403
    assert response.json()["detail"] == "authorization_scope_denied"


def test_admin_cli_issues_governed_identity_metadata(tmp_path):
    store_path = tmp_path / "keys.json"
    assert _cli([
        "--store", str(store_path), "issue",
        "--token", "secret-value", "--key-id", "bac-operations-1",
        "--tenant-id", TENANT, "--ttl-seconds", "300",
        "--scans-per-min", "2", "--max-concurrent", "1",
        *sum((["--scope", scope] for scope in SCOPES), []),
        "--issuer", "scanner-platform", "--custodian", "platform-maintainer",
        "--rotation-owner", "platform-maintainer",
        "--revocation-owner", "scanner-operator",
        "--revocation-procedure", "python -m api.edge_gateway --store /path/to/keys.json revoke --key-id bac-operations-1",
    ]) == 0
    record = KeyStore(store_path).list_records()[0]
    assert record.scopes == tuple(sorted(SCOPES))
    assert record.rate_limit == {"scans_per_min": 2, "max_concurrent": 1}
    assert record.issuer == "scanner-platform"
    assert record.custodian == "platform-maintainer"
    assert record.rotation_owner == "platform-maintainer"
    assert record.revocation_owner == "scanner-operator"
    assert "revoke --key-id bac-operations-1" in record.revocation_procedure


def test_admin_cli_without_scope_preserves_released_legacy_access(tmp_path):
    store_path = tmp_path / "keys.json"
    assert _cli([
        "--store", str(store_path), "issue",
        "--token", "legacy-cli-token", "--key-id", "legacy-cli-1",
        "--tenant-id", TENANT, "--ttl-seconds", "300",
    ]) == 0

    payload = json.loads(store_path.read_text())
    assert "scopes" not in payload["keys"]["legacy-cli-token"]

    app = create_app(
        rubric=Rubric.load(Path(__file__).parents[1] / "rubric" / "rubric.yaml"),
        bundles=BundleStore(), scan_runtime=None,
    )
    app.state.edge_gateway = EdgeGateway(
        store=KeyStore(store_path), limiter=RateLimiter(),
        defaults=RateLimitConfig(scans_per_min=10, max_concurrent=3),
    )
    client = TestClient(app, raise_server_exceptions=False)
    headers = {"Authorization": "Bearer legacy-cli-token", "X-Tenant-Id": TENANT}
    assert client.post("/scanner/v1/scans", headers=headers, json={}).status_code != 403
    assert client.get("/scan/example", headers=headers).status_code != 403
    assert client.get("/scanner/v1/scans/example/export", headers=headers).status_code != 403
    assert client.get("/scanner/v1/release-identity", headers=headers).status_code != 403
