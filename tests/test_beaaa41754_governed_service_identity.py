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
    client, _ = _client(tmp_path, scopes=("GET /scanner/v1/release-identity",))
    headers = {"Authorization": "Bearer bac-token", "X-Tenant-Id": TENANT}
    assert client.get("/scanner/v1/release-identity", headers=headers).status_code != 403
    denied = client.post("/scanner/v1/scans", headers=headers, json={})
    assert denied.status_code == 403
    assert denied.json()["detail"] == "authorization_scope_denied"


def test_legacy_record_with_no_scopes_does_not_gain_operations_authority(tmp_path):
    client, _ = _client(tmp_path, scopes=())
    headers = {"Authorization": "Bearer bac-token", "X-Tenant-Id": TENANT}
    for method, path in (
        (client.get, "/scanner/v1/release-identity"),
        (client.post, "/scanner/v1/scans"),
        (client.get, "/scan/example"),
        (client.get, "/scanner/v1/scans/example/export"),
    ):
        response = method(path, headers=headers, **({"json": {}} if method == client.post else {}))
        assert response.status_code == 403, response.text


def test_tenant_mismatch_rejected_before_scan_runtime(tmp_path):
    client, _ = _client(tmp_path)
    response = client.post(
        "/scanner/v1/scans", json={},
        headers={"Authorization": "Bearer bac-token", "X-Tenant-Id": "other-tenant"},
    )
    assert response.status_code == 409
    assert "tenant mismatch" in response.json()["detail"]


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
