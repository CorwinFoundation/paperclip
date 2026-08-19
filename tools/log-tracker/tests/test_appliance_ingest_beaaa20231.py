"""BEAAA-20231 production ingest adapter, driven by the REAL sealed contract.

Round 1 of BEAAA-20237 found this suite green and blind: it substituted a stub
verifier, a 6-element prohibited set, and a batch_seq the appliance never emits,
so it passed while the adapter rejected 100% of genuine sealed batches. Every
test below now loads the sealed appliance through ``load_sealed_contract`` -- the
exact path the systemd unit takes -- and feeds it batches built by the sealed
``BatchBuilder``. Synthetic Enso-shaped data only; no network, no Enso contact.
"""
from __future__ import annotations

import copy
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from log_tracker.appliance_ingest import (
    FIRST_BATCH_SEQ, ApplianceIngestStore, IngestRejected,
)
from log_tracker.appliance_ingest_server import load_sealed_contract


REPO = Path(__file__).resolve().parents[3]
SEALED = REPO / "ops/paperclip/numbat/backbond-appliance-0.1.0/backbond-appliance"
MIGRATION = Path(__file__).parents[1] / "migrations" / "0002_appliance_ingest.sql"

verify_batch, CONTENT_FIELDS = load_sealed_contract(SEALED)
if str(SEALED) not in sys.path:  # load_sealed_contract already inserted it
    sys.path.insert(0, str(SEALED))
from appliance import pipeline  # noqa: E402
from appliance.batch import BatchBuilder, Signer, _canonical, _sha256  # noqa: E402


SIGNED_BODY_FIELDS = ("batch_id", "appliance_id", "batch_seq", "prev_batch_hash",
                      "window", "events", "determinations", "counts", "producer")


def resign(batch: dict, signer: Signer) -> dict:
    """Re-sign a mutated batch exactly as BatchBuilder._make_batch does.

    Every rejection test below has to clear the sealed verifier first, otherwise
    it would prove only that a broken signature is caught -- which says nothing
    about the boundary, chain, or shape checks that run after it.
    """
    payload = _canonical({k: batch[k] for k in SIGNED_BODY_FIELDS})
    batch["batch_hash"] = _sha256(payload)
    batch["signature"] = signer.sign(payload)
    return batch


PRODUCER_PINS = {
    "collector_version": "appliance-0.1.0", "evidence_profile_version": "1.0",
    "scanner_version": "na-appliance", "corpus_version": "na-appliance",
    "rule_pack_version": "0.1", "seed": 0,
}


def test_sealed_prohibited_set_is_the_real_one():
    """Guards the round-1 defect directly: a 6-element stub set hid F1."""
    assert len(CONTENT_FIELDS) >= 24
    assert {"content", "message", "recipient", "argv"} <= set(CONTENT_FIELDS)


class Appliance:
    """A sealed appliance instance with durable chain state, as deployed."""

    def __init__(self, root: Path, appliance_id: str = "a1") -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.signer = Signer(str(root / "appliance.key"), "appliance-key-1")
        self.state_dir = str(root / "state")
        self.appliance_id = appliance_id
        self.public_key_pem = self.signer.public_pem()
        self._n = 0

    def emit(self, tenant: str = "t1", count: int = 2, etype: str = "social_post") -> dict:
        """Build one genuine signed batch from synthetic Enso raw events."""
        builder = BatchBuilder(self.appliance_id, self.signer, self.state_dir, PRODUCER_PINS)
        raw = []
        for _ in range(count):
            self._n += 1
            raw.append({
                "event_id": f"e{self._n}", "tenant_id": tenant, "agent_id": "growth-agent",
                "ts": "2026-08-17T00:00:00Z", "type": etype,
                "content": f"synthetic body {self._n}", "to": "synthetic@example.invalid",
                "billable": True,
            })
        emittable = [pipeline.redact(pipeline.normalize(r)) for r in raw]
        batches = builder.build(emittable, [], {"start": "x", "end": "y"})
        assert len(batches) == 1
        return batches[0]


@pytest.fixture
def appliance(tmp_path: Path) -> Appliance:
    return Appliance(tmp_path)


@pytest.fixture
def store(tmp_path: Path, appliance: Appliance) -> ApplianceIngestStore:
    result = ApplianceIngestStore(tmp_path / "tracker.sqlite3", MIGRATION,
                                  verify_batch=verify_batch, prohibited_fields=CONTENT_FIELDS)
    result.enroll(appliance_id="a1", tenant_id="t1", source_id="enso-synthetic",
                  token="secret", public_key_pem=appliance.public_key_pem)
    return result


def ingest(store: ApplianceIngestStore, batch: dict, token: str = "secret"):
    return store.ingest(batch, token=token, tenant_id="t1", source_id="enso-synthetic")


def test_genuine_first_batch_is_accepted(store, appliance):
    """F1 + F2 regression: the appliance's first batch carries batch_seq == 1 and a
    counts histogram keyed by action_class, one key of which is `message`."""
    batch = appliance.emit()
    assert batch["batch_seq"] == FIRST_BATCH_SEQ == 1
    assert "message" in batch["counts"]["by_action_class"]
    assert "message" in CONTENT_FIELDS
    assert verify_batch(batch, appliance.public_key_pem) is True

    assert ingest(store, batch).accepted
    assert store.status() == {"batches_accepted": 1, "events_accepted": 2,
                              "determinations_accepted": 0}


def test_genuine_chain_continues_and_survives_restart(store, appliance):
    first = appliance.emit()
    assert ingest(store, first).accepted
    second = appliance.emit()
    assert second["batch_seq"] == 2 and second["prev_batch_hash"] == first["batch_hash"]

    restarted = ApplianceIngestStore(store.database, MIGRATION,
                                     verify_batch=verify_batch, prohibited_fields=CONTENT_FIELDS)
    assert ingest(restarted, second).accepted
    assert ingest(restarted, first).duplicate
    with sqlite3.connect(store.database) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(appliance_ingest_receipts)")}
    assert not ({"events", "determinations", "content", "command", "payload"} & columns)
    assert restarted.status()["batches_accepted"] == 2


def test_out_of_order_genuine_batch_is_a_broken_chain(store, appliance):
    appliance.emit()                      # never delivered
    second = appliance.emit()
    with pytest.raises(IngestRejected, match="broken_chain"):
        ingest(store, second)
    assert store.status()["batches_accepted"] == 0


def test_content_boundary_still_rejects_a_validly_signed_leak(store, appliance):
    """The F1 fix must not open the boundary: a signed batch carrying real content
    is still rejected, and the rejection names the leaking path."""
    builder = BatchBuilder("a1", appliance.signer, appliance.state_dir, PRODUCER_PINS)
    leaky = [dict(pipeline.redact(pipeline.normalize(
        {"event_id": "leak", "tenant_id": "t1", "type": "social_post", "billable": True})),
        content="LEAKED CUSTOMER TEXT")]
    batch = builder.build(leaky, [], {"start": "x", "end": "y"})[0]
    assert verify_batch(batch, appliance.public_key_pem) is True
    with pytest.raises(IngestRejected, match=r"content_boundary_violated:\$\.events\[0\]\.content"):
        ingest(store, batch)


@pytest.mark.parametrize("histogram", [
    {"message": "LEAKED CUSTOMER TEXT"},          # value is not a count
    {"LEAKED CUSTOMER TEXT": 2},                  # key is not an emitted action class
    {"message": 99},                              # count disagrees with the events
    {},                                           # tally omitted while events exist
    "not-a-map",
])
def test_counts_histogram_may_not_diverge_from_the_events(store, appliance, histogram):
    """The F1 exemption is exact-equality, not a skip: nothing rides through here."""
    batch = appliance.emit()
    batch["counts"]["by_action_class"] = histogram
    resign(batch, appliance.signer)
    assert verify_batch(batch, appliance.public_key_pem) is True
    with pytest.raises(IngestRejected,
                       match=r"content_boundary_violated:\$\.counts\.by_action_class"):
        ingest(store, batch)


def test_boundary_is_checked_before_the_histogram(store, appliance):
    """Ordering matters: the histogram check trusts events the name scan cleared."""
    batch = appliance.emit()
    batch["events"][0]["message"] = "LEAKED"
    batch["counts"]["by_action_class"] = {"message": 99}
    resign(batch, appliance.signer)
    with pytest.raises(IngestRejected, match=r"content_boundary_violated:\$\.events\[0\]\.message"):
        ingest(store, batch)


def test_enrollment_restart_is_idempotent_but_rebinding_fails(store, appliance):
    store.enroll(appliance_id="a1", tenant_id="t1", source_id="enso-synthetic",
                 token="secret", public_key_pem=appliance.public_key_pem)
    with pytest.raises(ValueError, match="enrollment_binding_conflict"):
        store.enroll(appliance_id="a1", tenant_id="other", source_id="enso-synthetic",
                     token="secret", public_key_pem=appliance.public_key_pem)


def test_tamper_is_rejected_by_the_real_verifier_before_idempotency(store, appliance):
    batch = appliance.emit()
    assert ingest(store, batch).accepted
    tampered = copy.deepcopy(batch)
    tampered["counts"]["tokens"] = 999
    assert verify_batch(tampered, appliance.public_key_pem) is False
    with pytest.raises(IngestRejected, match="signature_or_batch_hash_invalid"):
        ingest(store, tampered)


def test_auth_and_scope_fail_closed(store, appliance):
    batch = appliance.emit()
    with pytest.raises(IngestRejected, match="unauthorized_scope"):
        ingest(store, batch, token="wrong")
    foreign = appliance.emit(tenant="t-other")
    with pytest.raises(IngestRejected, match="unauthorized_scope"):
        store.ingest(foreign, token="secret", tenant_id="t1", source_id="enso-synthetic")
    assert store.status()["batches_accepted"] == 0


@pytest.mark.parametrize("seq", ["1", 1.0, None, 0, -1, True])
def test_malformed_batch_seq_is_deterministic_not_retryable(store, appliance, seq):
    """F6: a permanently malformed batch must not be classified as a 503 outage."""
    batch = appliance.emit()
    batch["batch_seq"] = seq
    resign(batch, appliance.signer)
    assert verify_batch(batch, appliance.public_key_pem) is True
    with pytest.raises(IngestRejected, match="malformed_batch_seq"):
        ingest(store, batch)


@pytest.mark.parametrize("counts", [{"billable_actions": "2"}, {"tokens": -1}, {"tokens": None}])
def test_malformed_counts_are_deterministic_not_retryable(store, appliance, counts):
    batch = appliance.emit()
    batch["counts"].update(counts)
    resign(batch, appliance.signer)
    with pytest.raises(IngestRejected, match="malformed_counts"):
        ingest(store, batch)


def test_scoped_status_requires_the_enrolled_token_and_never_crosses_tenants(tmp_path):
    """F5: the only status path on the wire is authenticated and single-tenant."""
    a = Appliance(tmp_path / "a", "a1")
    b = Appliance(tmp_path / "b", "a2")
    store = ApplianceIngestStore(tmp_path / "t.sqlite3", MIGRATION,
                                 verify_batch=verify_batch, prohibited_fields=CONTENT_FIELDS)
    store.enroll(appliance_id="a1", tenant_id="tenantA", source_id="s",
                 token="tokA", public_key_pem=a.public_key_pem)
    store.enroll(appliance_id="a2", tenant_id="tenantB", source_id="s",
                 token="tokB", public_key_pem=b.public_key_pem)
    store.ingest(a.emit(tenant="tenantA", count=1), token="tokA",
                 tenant_id="tenantA", source_id="s")
    store.ingest(b.emit(tenant="tenantB", count=3), token="tokB",
                 tenant_id="tenantB", source_id="s")

    assert store.scoped_status(token="tokA", tenant_id="tenantA")["events_accepted"] == 1
    assert store.scoped_status(token="tokB", tenant_id="tenantB")["events_accepted"] == 3
    for token, tenant in [("", "tenantA"), ("tokB", "tenantA"), ("tokA", "tenantB")]:
        with pytest.raises(IngestRejected, match="unauthorized_scope"):
            store.scoped_status(token=token, tenant_id=tenant)
    assert store.status()["events_accepted"] == 4  # unscoped view is off the wire


def test_concurrent_redelivery_is_one_durable_receipt(store, appliance):
    batch = appliance.emit()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: ingest(store, copy.deepcopy(batch)), range(16)))
    assert sum(not result.duplicate for result in results) == 1
    assert store.status()["batches_accepted"] == 1


def test_persistence_failure_does_not_advance_state(store, appliance, monkeypatch):
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

    batch = appliance.emit()
    monkeypatch.setattr(store, "_connect", lambda: FailingConnection(original()))
    with pytest.raises(sqlite3.OperationalError, match="synthetic outage"):
        ingest(store, batch)
    monkeypatch.setattr(store, "_connect", original)
    assert ingest(store, batch).accepted
    assert json.loads(json.dumps(store.status()))["batches_accepted"] == 1
