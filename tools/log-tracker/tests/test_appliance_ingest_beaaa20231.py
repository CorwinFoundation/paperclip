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


# Both members of the sealed CONTENT_FIELDS. The payload is identical in every
# forgery test below; only the key it hangs off changes, so a pass can only come
# from the addressing mechanism and not from the payload.
FORGERY_PAYLOAD = {"message": "LEAKED CUSTOMER TEXT", "recipient": "victim@example.invalid"}


def snapshot_receipts(store: ApplianceIngestStore) -> list:
    with sqlite3.connect(store.database) as db:
        return db.execute(
            "SELECT * FROM appliance_ingest_receipts ORDER BY appliance_id,batch_seq"
        ).fetchall()


@pytest.mark.parametrize("forged_key", [
    "counts.by_action_class",       # renders to the exempt dotted path at depth 1
    "$.counts.by_action_class",     # ... including the root marker
    "extra",                        # control: an ordinary key was always caught
])
def test_a_dotted_top_level_key_cannot_forge_the_histogram_exemption(
    store, appliance, forged_key,
):
    """BEAAA-24478 item 1 / BEAAA-20237 F7 regression.

    The F1 exemption used to be addressed by a *rendered* dotted path, and the
    sender names its own keys, so a top-level key literally called
    ``counts.by_action_class`` rendered to the same string and carried its whole
    subtree past the scan unread. Nothing else caught it: ``verify_batch``
    rebuilds the signed body from nine named keys, so the injected key is
    unsigned and the batch still verifies, and ``_histogram_violation`` reads
    ``batch["counts"]``, a different key. No re-sign here -- that is the point.
    """
    batch = appliance.emit()
    batch[forged_key] = dict(FORGERY_PAYLOAD)
    assert verify_batch(batch, appliance.public_key_pem) is True, (
        "the injected key must sit outside the signature, or this proves nothing"
    )

    with pytest.raises(IngestRejected) as rejection:
        ingest(store, batch)
    reason = str(rejection.value)
    assert reason.startswith("content_boundary_violated:")
    # The reason names the forged key unambiguously, never the genuine address.
    assert forged_key in reason and "message" in reason
    assert store.status()["batches_accepted"] == 0


def test_the_forgery_is_rejected_even_when_it_rides_a_delivered_batchs_hash(store, appliance):
    """The injected key is outside ``batch_hash``, so the forgery shares the
    genuine batch's idempotency key. The boundary runs before the duplicate
    lookup, so the second delivery must still be refused, not waved through."""
    batch = appliance.emit()
    assert ingest(store, copy.deepcopy(batch)).accepted

    forged = dict(batch)
    forged["counts.by_action_class"] = dict(FORGERY_PAYLOAD)
    assert forged["batch_hash"] == batch["batch_hash"]
    assert verify_batch(forged, appliance.public_key_pem) is True
    with pytest.raises(IngestRejected, match="content_boundary_violated:"):
        ingest(store, forged)
    assert store.status()["batches_accepted"] == 1


def test_only_the_top_level_histogram_is_exempt_not_a_lookalike_deeper_in(store, appliance):
    """The exemption is one structural address, not a shape. A ``counts`` map
    that happens to appear inside an event is scanned like anything else."""
    batch = appliance.emit()
    batch["events"][0]["counts"] = {"by_action_class": {"message": "LEAKED CUSTOMER TEXT"}}
    resign(batch, appliance.signer)
    assert verify_batch(batch, appliance.public_key_pem) is True
    with pytest.raises(
        IngestRejected,
        match=r"content_boundary_violated:\$\.events\[0\]\.counts\.by_action_class\.message",
    ):
        ingest(store, batch)
    assert store.status()["batches_accepted"] == 0


def test_a_genuine_multi_agent_envelope_is_accepted(store, appliance):
    """BEAAA-24478 item 2. ``agent_id`` is per-event in the sealed ``_make_batch``,
    so one genuine 0.1.0 envelope spans agents. The atomic reject stays on the
    tenant limb only; widening it to the agent limb would reject the normal
    envelope and take ingest to zero throughput. Gate 1's agent/binding limbs are
    producer obligations for a future 0.1.1 that groups at source."""
    builder = BatchBuilder("a1", appliance.signer, appliance.state_dir, PRODUCER_PINS)
    raw = [{"event_id": f"e-{agent}", "tenant_id": "t1", "agent_id": agent,
            "ts": "2026-08-17T00:00:00Z", "type": "social_post",
            "content": f"synthetic body for {agent}", "billable": True}
           for agent in ("ag1", "ag2")]
    batch = builder.build([pipeline.redact(pipeline.normalize(r)) for r in raw],
                          [], {"start": "x", "end": "y"})[0]
    assert {event["agent_id"] for event in batch["events"]} == {"ag1", "ag2"}
    assert {event["tenant_id"] for event in batch["events"]} == {"t1"}

    assert ingest(store, batch).accepted
    assert store.status()["events_accepted"] == 2


@pytest.mark.parametrize("break_it,reason", [
    (lambda b, s: b.__setitem__("counts.by_action_class", dict(FORGERY_PAYLOAD)),
     "content_boundary_violated"),
    (lambda b, s: (b["events"][0].__setitem__("tenant_id", "tenantA"), resign(b, s)),
     "unauthorized_scope"),
    (lambda b, s: (b.__setitem__("batch_seq", 0), resign(b, s)), "malformed_batch_seq"),
    (lambda b, s: (b.__setitem__("prev_batch_hash", "nope"), resign(b, s)), "broken_chain"),
])
def test_a_rejected_envelope_mutates_no_other_appliances_ledger(tmp_path, break_it, reason):
    """BEAAA-20236 option-A gate 4. Whatever the rejection reason, a refused
    envelope leaves every other appliance's receipts byte-identical, and leaves
    no row of its own -- verification and shape checks all precede the write
    transaction, and the write is atomic."""
    a = Appliance(tmp_path / "a", "a1")
    b = Appliance(tmp_path / "b", "a2")
    store = ApplianceIngestStore(tmp_path / "t.sqlite3", MIGRATION,
                                 verify_batch=verify_batch, prohibited_fields=CONTENT_FIELDS)
    store.enroll(appliance_id="a1", tenant_id="tenantA", source_id="s",
                 token="tokA", public_key_pem=a.public_key_pem)
    store.enroll(appliance_id="a2", tenant_id="tenantB", source_id="s",
                 token="tokB", public_key_pem=b.public_key_pem)
    store.ingest(a.emit(tenant="tenantA", count=2), token="tokA",
                 tenant_id="tenantA", source_id="s")
    before = snapshot_receipts(store)
    assert len(before) == 1

    bad = b.emit(tenant="tenantB", count=3)
    break_it(bad, b.signer)
    with pytest.raises(IngestRejected, match=reason):
        store.ingest(bad, token="tokB", tenant_id="tenantB", source_id="s")

    assert snapshot_receipts(store) == before
    assert store.scoped_status(token="tokA", tenant_id="tenantA")["events_accepted"] == 2
    assert store.scoped_status(token="tokB", tenant_id="tenantB")["events_accepted"] == 0


def test_enrollment_is_the_p1_single_tenant_invariant(store, appliance):
    """BEAAA-24478 item 3 / PRODUCTION-INGEST.md "P1 enrollment invariant".
    An appliance_id is bound to exactly one tenant_id for the life of the
    enrollment row; rebinding is refused rather than silently widening scope.
    That, not a wire-level check, is what makes a mixed-tenant envelope
    unreachable in P1."""
    with pytest.raises(ValueError, match="enrollment_binding_conflict"):
        store.enroll(appliance_id="a1", tenant_id="t-second", source_id="enso-synthetic",
                     token="secret", public_key_pem=appliance.public_key_pem)
    # ... and the original binding still holds, so a batch for the second tenant
    # cannot be delivered under this appliance at all.
    foreign = appliance.emit(tenant="t-second")
    with pytest.raises(IngestRejected, match="unauthorized_scope"):
        store.ingest(foreign, token="secret", tenant_id="t-second", source_id="enso-synthetic")


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
