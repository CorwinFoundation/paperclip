from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from log_tracker.appliance_ingest import ApplianceIngestStore, IngestRejected


MIGRATION = Path(__file__).parents[1] / "migrations" / "0002_appliance_ingest.sql"
PROHIBITED = {"content", "command", "message", "prompt", "response", "text"}


def canonical_hash(batch: dict) -> str:
    body = {k: v for k, v in batch.items() if k not in {"batch_hash", "signature"}}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def sign(batch: dict) -> dict:
    batch["batch_hash"] = canonical_hash(batch)
    batch["signature"] = "valid:" + batch["batch_hash"]
    return batch


def verify(batch: dict, _public_key: str) -> bool:
    return batch.get("batch_hash") == canonical_hash(batch) and batch.get("signature") == "valid:" + batch["batch_hash"]


def make_batch(seq: int = 0, prev: str | None = None) -> dict:
    return sign({
        "contract_version": "contract-v1.0", "appliance_id": "a1",
        "batch_seq": seq, "prev_batch_hash": prev,
        "counts": {"billable_actions": 2, "tokens": 3},
        "events": [{"tenant_id": "t1", "agent_id": "agent", "event_id": "e1", "evidence_hash": "h1"}],
        "determinations": [{"tenant_id": "t1", "agent_id": "agent", "event_id": "e1", "rule_id": "r1", "severity": "low", "evidence_hash": "h1"}],
    })


@pytest.fixture
def store(tmp_path: Path) -> ApplianceIngestStore:
    result = ApplianceIngestStore(tmp_path / "tracker.sqlite3", MIGRATION, verify_batch=verify, prohibited_fields=PROHIBITED)
    result.enroll(appliance_id="a1", tenant_id="t1", source_id="enso-synthetic", token="secret", public_key_pem="pk")
    return result


def ingest(store: ApplianceIngestStore, batch: dict, token: str = "secret"):
    return store.ingest(batch, token=token, tenant_id="t1", source_id="enso-synthetic")


def test_accepts_counts_and_hashes_only_and_survives_restart(store: ApplianceIngestStore):
    batch = make_batch()
    assert ingest(store, batch).accepted
    restarted = ApplianceIngestStore(store.database, MIGRATION, verify_batch=verify, prohibited_fields=PROHIBITED)
    assert ingest(restarted, batch).duplicate
    with sqlite3.connect(store.database) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(appliance_ingest_receipts)")}
        assert not ({"events", "determinations", "content", "command", "payload"} & columns)
    assert restarted.status() == {"batches_accepted": 1, "events_accepted": 1, "determinations_accepted": 1}


def test_enrollment_restart_is_idempotent_but_rebinding_fails(store):
    store.enroll(appliance_id="a1", tenant_id="t1", source_id="enso-synthetic", token="secret", public_key_pem="pk")
    with pytest.raises(ValueError, match="enrollment_binding_conflict"):
        store.enroll(appliance_id="a1", tenant_id="other", source_id="enso-synthetic", token="secret", public_key_pem="pk")


@pytest.mark.parametrize("mutation,reason", [
    (lambda b: b["counts"].__setitem__("tokens", 99), "signature_or_batch_hash_invalid"),
    (lambda b: b["events"][0].__setitem__("content", "forbidden"), "content_boundary_violated"),
])
def test_rejects_tamper_and_validly_signed_nested_content(store, mutation, reason):
    batch = make_batch()
    mutation(batch)
    if reason == "content_boundary_violated":
        sign(batch)
    with pytest.raises(IngestRejected, match=reason):
        ingest(store, batch)
    assert store.status()["batches_accepted"] == 0


def test_tampered_duplicate_rejects_before_idempotency(store):
    batch = make_batch()
    ingest(store, batch)
    tampered = copy.deepcopy(batch)
    tampered["counts"]["tokens"] = 999
    with pytest.raises(IngestRejected, match="signature_or_batch_hash_invalid"):
        ingest(store, tampered)


def test_auth_chain_and_scope_fail_closed(store):
    with pytest.raises(IngestRejected, match="unauthorized_scope"):
        ingest(store, make_batch(), token="wrong")
    with pytest.raises(IngestRejected, match="broken_chain"):
        ingest(store, make_batch(seq=1, prev="wrong"))
    outside = make_batch()
    outside["events"][0]["tenant_id"] = "other"
    sign(outside)
    with pytest.raises(IngestRejected, match="unauthorized_scope"):
        ingest(store, outside)


def test_concurrent_redelivery_is_one_durable_receipt(store):
    batch = make_batch()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: ingest(store, copy.deepcopy(batch)), range(16)))
    assert sum(not result.duplicate for result in results) == 1
    assert store.status()["batches_accepted"] == 1


def test_persistence_failure_does_not_advance_state(store, monkeypatch):
    original = store._connect
    class FailingConnection:
        def __init__(self, wrapped): self.wrapped = wrapped
        def __getattr__(self, name): return getattr(self.wrapped, name)
        def execute(self, sql, params=()):
            if sql.startswith("INSERT INTO appliance_ingest_receipts"):
                raise sqlite3.OperationalError("synthetic outage")
            return self.wrapped.execute(sql, params)
        def __enter__(self): return self
        def __exit__(self, *args): self.wrapped.close()
    monkeypatch.setattr(store, "_connect", lambda: FailingConnection(original()))
    with pytest.raises(sqlite3.OperationalError, match="synthetic outage"):
        ingest(store, make_batch())
    monkeypatch.setattr(store, "_connect", original)
    assert ingest(store, make_batch()).accepted
