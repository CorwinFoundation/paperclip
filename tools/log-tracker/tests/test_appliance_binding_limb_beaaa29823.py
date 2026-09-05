"""BEAAA-29823 unit (a): the binding limb of the ADR BEAAA-20236 option-A key.

The limb is resolved server-side from the authenticated ``appliance_id``. There
is no producer change and no wire change -- ``EMIT_ALLOWLIST`` carries no binding
identifier through appliance 0.1.3, and ``verify_batch`` rebuilds the signed body
from nine named keys, none of them a binding. These tests pin that the resolved
value is the revision in force when the batch *window opened*, not the current
one (ADR rule 7), and that there is no fallback when no revision was in force.
"""
from __future__ import annotations

import sqlite3

import pytest

from log_tracker.appliance_ingest import ApplianceIngestStore, IngestRejected, _digest
from test_appliance_ingest_beaaa20231 import (
    CONTENT_FIELDS, MIGRATIONS, Appliance, WINDOW, resign, verify_batch,
)

EPOCH = "1970-01-01T00:00:00Z"
BEFORE = {"start": "2026-08-17T00:00:00Z", "end": "2026-08-17T01:00:00Z"}
AFTER = {"start": "2099-01-01T00:00:00Z", "end": "2099-01-01T01:00:00Z"}


def build_store(tmp_path, appliance, *, binding="BND-0001", effective=EPOCH):
    store = ApplianceIngestStore(tmp_path / "tracker.sqlite3", MIGRATIONS,
                                 verify_batch=verify_batch, prohibited_fields=CONTENT_FIELDS)
    store.enroll(appliance_id=appliance.appliance_id, tenant_id="t1",
                 source_id="enso-synthetic", token="secret",
                 public_key_pem=appliance.public_key_pem,
                 binding_id=binding, binding_effective_from=effective)
    return store


def deliver(store, batch):
    return store.ingest(batch, token="secret", tenant_id="t1", source_id="enso-synthetic")


def bindings(store):
    with sqlite3.connect(store.database) as db:
        return db.execute("SELECT batch_hash,binding_id_hash,effective_from "
                          "FROM appliance_ingest_receipt_bindings").fetchall()


def receipts(store):
    with sqlite3.connect(store.database) as db:
        return db.execute("SELECT COUNT(*) FROM appliance_ingest_receipts").fetchone()[0]


@pytest.fixture
def appliance(tmp_path):
    return Appliance(tmp_path)


def test_an_accepted_batch_carries_exactly_one_hashed_binding_row(tmp_path, appliance):
    store = build_store(tmp_path, appliance)
    batch = appliance.emit()
    assert deliver(store, batch).accepted
    rows = bindings(store)
    assert len(rows) == 1
    batch_hash, binding_hash, effective = rows[0]
    assert batch_hash == batch["batch_hash"]
    assert binding_hash == _digest("BND-0001")
    assert "BND-0001" not in binding_hash  # hashed on the way in, never plaintext
    assert effective == EPOCH


def test_the_same_binding_hashes_alike_across_appliances(tmp_path):
    """Deliberately *unsalted*, unlike the agent limb.

    BEAAA-24613 salts ``agent_id_hash`` per appliance because customer agent
    names are low-entropy and correlating them across appliances is the threat.
    A binding identifier is BackBond's own and is the reconciliation key itself:
    it has to join across every appliance under one binding, so a per-appliance
    salt would destroy the only property the limb exists for. Same treatment as
    ``tenant_id_hash`` and ``source_id_hash``: plain sha256.
    """
    one, two = Appliance(tmp_path / "one"), Appliance(tmp_path / "two", appliance_id="a2")
    store = build_store(tmp_path / "one", one)
    store.enroll(appliance_id="a2", tenant_id="t1", source_id="enso-synthetic",
                 token="secret", public_key_pem=two.public_key_pem,
                 binding_id="BND-0001", binding_effective_from=EPOCH)
    assert deliver(store, one.emit()).accepted
    assert deliver(store, two.emit()).accepted
    assert len({row[1] for row in bindings(store)}) == 1


def test_a_late_batch_resolves_to_the_binding_its_window_ran_under(tmp_path, appliance):
    """ADR BEAAA-20236 rule 7. The batch is delivered *after* the re-binding and
    still belongs to the previous binding, because its window opened there."""
    store = build_store(tmp_path, appliance)
    appliance.window = BEFORE
    early = appliance.emit()
    store.enroll(appliance_id="a1", tenant_id="t1", source_id="enso-synthetic",
                 token="secret", public_key_pem=appliance.public_key_pem,
                 binding_id="BND-0002")
    assert deliver(store, early).accepted
    assert bindings(store)[0][1] == _digest("BND-0001")


def test_a_window_that_opens_after_the_rebinding_takes_the_new_binding(tmp_path, appliance):
    store = build_store(tmp_path, appliance)
    appliance.window = BEFORE
    assert deliver(store, appliance.emit()).accepted
    store.enroll(appliance_id="a1", tenant_id="t1", source_id="enso-synthetic",
                 token="secret", public_key_pem=appliance.public_key_pem,
                 binding_id="BND-0002")
    appliance.window = AFTER
    assert deliver(store, appliance.emit()).accepted
    resolved = {row[1] for row in bindings(store)}
    assert resolved == {_digest("BND-0001"), _digest("BND-0002")}


def test_re_enrolling_the_same_binding_appends_no_revision(tmp_path, appliance):
    store = build_store(tmp_path, appliance)
    for _ in range(3):
        store.enroll(appliance_id="a1", tenant_id="t1", source_id="enso-synthetic",
                     token="secret", public_key_pem=appliance.public_key_pem,
                     binding_id="BND-0001")
    with sqlite3.connect(store.database) as db:
        assert db.execute("SELECT COUNT(*) FROM appliance_enrollment_bindings").fetchone()[0] == 1


def test_a_window_before_the_first_revision_is_refused_with_no_fallback(tmp_path, appliance):
    """ADR line 74 forbids an alias, a nullable alternative, or a fallback, so
    there is nothing to attribute the batch to and it is refused outright.
    Attributing it to the current binding is the shift rule 7 exists to stop."""
    store = build_store(tmp_path, appliance, effective="2099-06-01T00:00:00Z")
    appliance.window = BEFORE
    with pytest.raises(IngestRejected) as raised:
        deliver(store, appliance.emit())
    assert str(raised.value) == "unenrolled_binding"
    assert receipts(store) == 0 and bindings(store) == []


@pytest.mark.parametrize("window", [{}, {"end": "2026-08-17T01:00:00Z"},
                                    {"start": "x", "end": "y"}, {"start": 17}, "2026-08-17"])
def test_a_window_without_a_readable_start_is_deterministic_not_retryable(
    tmp_path, appliance, window,
):
    """Malformed shape is a deterministic refusal, never a retryable unavailable
    (BEAAA-20237 F6). ``window`` sits inside the signed body, so a batch that
    reaches this branch is a producer defect and re-delivering it cannot help."""
    store = build_store(tmp_path, appliance)
    batch = appliance.emit()
    batch["window"] = window
    with pytest.raises(IngestRejected) as raised:
        deliver(store, resign(batch, appliance.signer))
    assert str(raised.value) == "malformed_window"
    assert receipts(store) == 0


def test_a_sender_supplied_binding_is_not_a_channel(tmp_path, appliance):
    """The limb is resolved from the authenticated appliance_id and from nothing
    the sender names. A batch that volunteers one is refused as the unknown
    envelope field it is, and the stored limb is still the enrolled one."""
    store = build_store(tmp_path, appliance)
    batch = appliance.emit()
    batch["binding_id"] = "BND-9999"
    with pytest.raises(IngestRejected) as raised:
        deliver(store, resign(batch, appliance.signer))
    assert str(raised.value) == "unknown_envelope_field"
    assert receipts(store) == 0


def test_enrollment_without_a_binding_is_incomplete(tmp_path, appliance):
    store = ApplianceIngestStore(tmp_path / "tracker.sqlite3", MIGRATIONS,
                                 verify_batch=verify_batch, prohibited_fields=CONTENT_FIELDS)
    with pytest.raises(TypeError):
        store.enroll(appliance_id="a1", tenant_id="t1", source_id="enso-synthetic",
                     token="secret", public_key_pem=appliance.public_key_pem)
    with pytest.raises(ValueError, match="complete enrollment required"):
        store.enroll(appliance_id="a1", tenant_id="t1", source_id="enso-synthetic",
                     token="secret", public_key_pem=appliance.public_key_pem, binding_id="")


def test_the_binding_tables_are_append_only(tmp_path, appliance):
    store = build_store(tmp_path, appliance)
    assert deliver(store, appliance.emit()).accepted
    with sqlite3.connect(store.database) as db:
        for sql in ("UPDATE appliance_enrollment_bindings SET binding_id_hash='x'",
                    "DELETE FROM appliance_enrollment_bindings",
                    "UPDATE appliance_ingest_receipt_bindings SET binding_id_hash='x'",
                    "DELETE FROM appliance_ingest_receipt_bindings"):
            with pytest.raises(sqlite3.IntegrityError, match="append_only"):
                db.execute(sql)


def test_an_absent_window_never_reaches_the_binding_branch(tmp_path, appliance):
    """It cannot: the sealed verifier rebuilds the signed body from nine named
    keys and ``window`` is one of them, so a batch missing it is unsignable and
    is refused a step earlier. Pinned so the branch above is never mistaken for
    the control that stops it."""
    store = build_store(tmp_path, appliance)
    batch = appliance.emit()
    batch.pop("window")
    with pytest.raises(IngestRejected) as raised:
        deliver(store, batch)
    assert str(raised.value) == "signature_or_batch_hash_invalid"
    assert receipts(store) == 0
