"""BEAAA-41663 P03 durable request identity acceptance tests."""

from concurrent.futures import ThreadPoolExecutor
import inspect

import pytest

from api.accepted_scan_store import AcceptedScanStore, RequestIdentityConflict
from api.scan_handlers import _kickoff_live_probe_scan


PINS = {
    "target": {"agent_id": "agent-1", "endpoint": "https://agent.example"},
    "release": {"scanner_version": "0.5.0", "corpus_version": "0.2.0"},
    "scope": {"mode": "full", "case_manifest": "registry@1"},
}


def test_public_scan_intake_reserves_identity_before_kickoff() -> None:
    """The public POST path must use the durable reservation, not just expose it."""
    source = inspect.getsource(_kickoff_live_probe_scan)
    reservation = source.index("reserve_request_identity")
    paid_work = source.index("result = runtime.kickoff")
    assert "client_request_id" in source
    assert reservation < paid_work


def test_concurrent_identical_retries_bind_one_scan_id(tmp_path) -> None:
    store = AcceptedScanStore(tmp_path / "accepted.sqlite3")

    def reserve(candidate: str):
        return store.reserve_request_identity(
            tenant_id="tenant-a",
            client_request_id="build-42",
            canonical_request_hash="sha256:" + "a" * 64,
            candidate_scan_id=candidate,
            pins=PINS,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(reserve, [f"scan-{i}" for i in range(8)]))

    assert len({row["scan_id"] for row in rows}) == 1
    assert sum(not row["replayed"] for row in rows) == 1
    assert all(row["pins"] == PINS for row in rows)


def test_binding_survives_restart_and_conflicting_reuse_rejects(tmp_path) -> None:
    path = tmp_path / "accepted.sqlite3"
    first = AcceptedScanStore(path)
    accepted = first.reserve_request_identity(
        tenant_id="tenant-a",
        client_request_id="build-42",
        canonical_request_hash="sha256:" + "a" * 64,
        candidate_scan_id="scan-original",
        pins=PINS,
    )
    first.close()

    restarted = AcceptedScanStore(path)
    replay = restarted.reserve_request_identity(
        tenant_id="tenant-a",
        client_request_id="build-42",
        canonical_request_hash="sha256:" + "a" * 64,
        candidate_scan_id="scan-duplicate",
        pins=PINS,
    )
    assert replay == {**accepted, "replayed": True}

    with pytest.raises(RequestIdentityConflict):
        restarted.reserve_request_identity(
            tenant_id="tenant-a",
            client_request_id="build-42",
            canonical_request_hash="sha256:" + "b" * 64,
            candidate_scan_id="scan-conflict",
            pins=PINS,
        )


def test_same_client_request_id_is_tenant_scoped(tmp_path) -> None:
    store = AcceptedScanStore(tmp_path / "accepted.sqlite3")
    a = store.reserve_request_identity(
        tenant_id="tenant-a",
        client_request_id="build-42",
        canonical_request_hash="sha256:" + "a" * 64,
        candidate_scan_id="scan-a",
        pins=PINS,
    )
    b = store.reserve_request_identity(
        tenant_id="tenant-b",
        client_request_id="build-42",
        canonical_request_hash="sha256:" + "b" * 64,
        candidate_scan_id="scan-b",
        pins=PINS,
    )
    assert a["scan_id"] == "scan-a"
    assert b["scan_id"] == "scan-b"
