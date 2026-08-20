"""BEAAA-24725: the closed ingest envelope and the P1 observability surface.

Scope comes from the CTO ruling on BEAAA-24623 (ADR
`ADR-BEAAA-24623-p1-acceptance-scope-ruling.md`), which redefined three of the
four open BEAAA-20237 P1 acceptance checks:

* evidence-tier spoof -> a closed top-level envelope, not a tier import;
* observability -> a structured outcome line plus rejection/duplicate/outage
  counters and the chain tip, not `/healthz` + `/metrics` as they stood;
* rollback -> a drill, evidenced separately in `artifacts/BEAAA-24725/`.

The sealed appliance is loaded exactly as the systemd unit loads it, and every
batch is built by the sealed ``BatchBuilder``, so these tests fail if the
contract moves rather than passing against a local idea of it. The BEAAA-20231
suite is imported rather than re-implemented: it already owns the harness, and
its own coverage stays green unmodified.
"""
from __future__ import annotations

import copy
import io
import json
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from log_tracker.appliance_ingest import (
    SEALED_ENVELOPE_FIELDS, SIGNED_BODY_FIELDS, ApplianceIngestStore,
    IngestRejected, OutcomeCounters, outcome_log_line, reason_class,
)
from log_tracker import appliance_ingest as ingest_module
from log_tracker import appliance_ingest_server as server_module
from log_tracker.appliance_ingest_server import make_handler

from test_appliance_ingest_beaaa20231 import (  # noqa: F401  (fixtures)
    CONTENT_FIELDS, MIGRATION, Appliance, appliance, ingest, resign, store,
    verify_batch,
)


# ---------------------------------------------------------------------------
# Part A -- the envelope is closed at the top level
# ---------------------------------------------------------------------------

def test_a_genuine_sealed_envelope_carries_exactly_the_pinned_keys(appliance):
    """The allowlist is pinned against the sealed producer, not against a doc.

    A 0.1.1 that adds a field trips this test red and has to be ruled on, which
    is the point: the alternative is the new field being accepted unsigned.
    """
    batch = appliance.emit()
    assert set(batch) == set(SEALED_ENVELOPE_FIELDS)
    assert len(SEALED_ENVELOPE_FIELDS) == 12
    # Nine signed, three appended after hashing.
    assert set(SIGNED_BODY_FIELDS) < set(SEALED_ENVELOPE_FIELDS)
    assert set(SEALED_ENVELOPE_FIELDS) - set(SIGNED_BODY_FIELDS) == {
        "batch_hash", "signature", "key_id"}


def test_an_evidence_tier_rider_is_rejected(store, appliance):
    """The check BEAAA-20237 asked for, in the form the ruling gave it.

    Before this, the rider verified and was accepted: it is outside the nine
    signed keys, and `evidence_tier` is in neither CONTENT_FIELDS nor any
    scanned subtree, so nothing looked at it.
    """
    batch = appliance.emit()
    batch["evidence_tier"] = {"tier": "attested", "attested_by": "self"}
    assert verify_batch(batch, appliance.public_key_pem) is True, (
        "the rider must sit outside the signature, or this proves nothing")

    with pytest.raises(IngestRejected, match=r"\Aunknown_envelope_field\Z"):
        ingest(store, batch)
    assert store.status()["batches_accepted"] == 0


@pytest.mark.parametrize("field", [
    "evidence_tier", "tier", "evidence_profile", "priority", "x",
    "batch_hash_", "_batch_hash", "BATCH_HASH", "counts2", "",
])
def test_any_key_outside_the_pinned_set_is_refused(store, appliance, field):
    """Set comparison against the allowlist, so the field nobody named is the
    field this closes. None of these carries a prohibited content name, so the
    content scan does not see them."""
    batch = appliance.emit()
    batch[field] = "anything"
    assert verify_batch(batch, appliance.public_key_pem) is True
    with pytest.raises(IngestRejected, match=r"\Aunknown_envelope_field\Z"):
        ingest(store, batch)


def test_the_rejection_names_no_sender_chosen_string(store, appliance):
    """The sender names its own keys. Echoing one into the reason would make the
    rejection the content channel the rendered path used to be."""
    secret = "LEAKING-VIA-THE-KEY-NAME"
    batch = appliance.emit()
    batch[secret] = 1
    with pytest.raises(IngestRejected) as rejection:
        ingest(store, batch)
    assert str(rejection.value) == "unknown_envelope_field"
    assert secret not in str(rejection.value)


def test_the_envelope_check_runs_after_signature_verification(store, appliance):
    """Ruling: after verification, before the write transaction. An unverifiable
    batch must report the verifier's reason, so the closed set never becomes a
    pre-auth oracle telling an unauthenticated sender which fields exist."""
    batch = appliance.emit()
    batch["evidence_tier"] = "attested"
    batch["counts"]["tokens"] = 999            # breaks the hash, not re-signed
    assert verify_batch(batch, appliance.public_key_pem) is False
    with pytest.raises(IngestRejected, match="signature_or_batch_hash_invalid"):
        ingest(store, batch)


def test_a_rider_riding_a_delivered_batchs_hash_is_still_refused(store, appliance):
    """The rider is outside ``batch_hash``, so it shares the genuine batch's
    idempotency key. The envelope check precedes the duplicate lookup, so the
    second delivery is refused rather than answered ``duplicate: true``."""
    batch = appliance.emit()
    assert ingest(store, copy.deepcopy(batch)).accepted

    forged = dict(batch)
    forged["evidence_tier"] = "attested"
    assert forged["batch_hash"] == batch["batch_hash"]
    assert verify_batch(forged, appliance.public_key_pem) is True
    with pytest.raises(IngestRejected, match=r"\Aunknown_envelope_field\Z"):
        ingest(store, forged)
    assert store.status()["batches_accepted"] == 1


def test_a_refused_envelope_leaves_no_receipt_row(store, appliance):
    """The refusal precedes the write transaction, and it consumes nothing: the
    same batch without the rider still delivers at seq 1 afterwards."""
    genuine = appliance.emit()
    riding = copy.deepcopy(genuine)
    riding["evidence_tier"] = "attested"
    with pytest.raises(IngestRejected, match=r"\Aunknown_envelope_field\Z"):
        ingest(store, riding)
    with sqlite3.connect(store.database) as db:
        assert db.execute("SELECT COUNT(*) FROM appliance_ingest_receipts").fetchone()[0] == 0
    assert ingest(store, genuine).accepted is True


# ``appliance_id`` is excluded: the enrollment lookup reads it before the
# verifier runs, so dropping it is an `unknown_appliance` refusal instead --
# covered directly below.
@pytest.mark.parametrize("dropped", tuple(
    f for f in SIGNED_BODY_FIELDS if f != "appliance_id") + ("batch_hash", "signature"))
def test_a_missing_envelope_key_is_already_caught_by_the_verifier(store, appliance, dropped):
    """Why the check tests for extras rather than for set equality: post
    verification the two are the same test. ``verify_batch`` rebuilds the signed
    body from nine named keys and reads ``batch_hash`` and ``signature``, so a
    batch missing any of them cannot reach the envelope check at all."""
    batch = appliance.emit()
    del batch[dropped]
    assert verify_batch(batch, appliance.public_key_pem) is False
    with pytest.raises(IngestRejected, match="signature_or_batch_hash_invalid"):
        ingest(store, batch)


def test_a_missing_appliance_id_is_refused_before_the_verifier(store, appliance):
    batch = appliance.emit()
    del batch["appliance_id"]
    with pytest.raises(IngestRejected, match="unknown_appliance"):
        ingest(store, batch)


def test_a_short_envelope_missing_only_key_id_is_still_accepted(store, appliance):
    """The one place the check's shape is visible, recorded rather than hidden.

    It tests for extra keys, not for exact set equality, so a batch missing
    ``key_id`` -- the only pinned key neither the verifier nor this adapter
    reads -- is accepted. That is not the class this closes: dropping a field
    removes information, where the residual is a sender *adding* an unsigned
    one. Every other pinned key is load-bearing to ``verify_batch`` and its
    absence is already a refusal, above. If P1 wants exact equality it is a
    one-word change and a second reason class, and that is a CTO call, not one
    to make silently inside an implementation ticket."""
    batch = appliance.emit()
    del batch["key_id"]
    assert verify_batch(batch, appliance.public_key_pem) is True
    assert ingest(store, batch).accepted is True


def test_the_reject_is_keyed_on_the_pinned_set_and_nothing_else(
    store, appliance, monkeypatch,
):
    """Control. Widen the pinned set and the same rider is accepted, so the
    refusal above comes from this constant and not from some other check that
    happens to fire on the same batch."""
    batch = appliance.emit()
    batch["evidence_tier"] = "attested"
    monkeypatch.setattr(
        ingest_module, "SEALED_ENVELOPE_FIELDS",
        frozenset(SEALED_ENVELOPE_FIELDS | {"evidence_tier"}))
    assert ingest(store, batch).accepted is True


def test_a_rider_whose_subtree_leaks_content_reports_the_leak(store, appliance):
    """Both checks refuse the delivery; the order decides only the reason class.
    A key carrying prohibited content is reported as the leak it is, which is
    also what keeps the BEAAA-24478 item 1 forgery regressions unmodified."""
    batch = appliance.emit()
    batch["evidence_tier"] = {"message": "LEAKED CUSTOMER TEXT"}
    with pytest.raises(IngestRejected, match=r"\Acontent_boundary_violated:"):
        ingest(store, batch)


# ---------------------------------------------------------------------------
# Part B1 -- one structured line per request outcome
# ---------------------------------------------------------------------------

FIXED_NOW = datetime(2026, 8, 19, 21, 30, 5, 123456, tzinfo=timezone.utc)


def test_the_outcome_line_is_one_json_object_with_the_named_fields():
    line = outcome_log_line(outcome="accepted", appliance_id="a1", batch_seq=7,
                            now=FIXED_NOW)
    assert "\n" not in line
    assert json.loads(line) == {
        "ts": "2026-08-19T21:30:05.123Z",
        "event": "appliance_ingest",
        "outcome": "accepted",
        "reason_class": None,
        "appliance_id": "a1",
        "batch_seq": 7,
    }


@pytest.mark.parametrize("outcome", ["accepted", "duplicate", "rejected", "unavailable"])
def test_every_named_outcome_is_writable(outcome):
    assert json.loads(outcome_log_line(outcome=outcome))["outcome"] == outcome


def test_an_unnamed_outcome_is_a_programming_error_not_a_log_line():
    with pytest.raises(ValueError):
        outcome_log_line(outcome="ok")


def test_the_line_carries_the_reason_class_never_the_rendered_path():
    """`content_boundary_violated:$.a.b` embeds keys the sender named, so a
    rendered path in a log turns the log into the content channel the boundary
    exists to deny."""
    leaked = "content_boundary_violated:$.events[0].VICTIM_CHOSE_THIS"
    record = json.loads(outcome_log_line(outcome="rejected", reason=leaked))
    assert record["reason_class"] == "content_boundary_violated"
    assert "VICTIM_CHOSE_THIS" not in json.dumps(record)


@pytest.mark.parametrize("reason,expected", [
    ("unknown_envelope_field", "unknown_envelope_field"),
    ("malformed_counts:tokens", "malformed_counts"),
    ("unauthorized_scope", "unauthorized_scope"),
    (None, None),
])
def test_reason_class_drops_every_rendered_suffix(reason, expected):
    assert reason_class(reason) == expected


@pytest.mark.parametrize("forged", [
    "a1\nts=2026-01-01T00:00:00Z outcome=accepted",   # a second, forged record
    "a" * 65,                                          # unbounded free text
    "tenant:acme secret",                              # space is off the alphabet
    {"nested": "object"}, 17, None, "",
])
def test_a_sender_named_appliance_id_cannot_become_a_log_channel(forged):
    """``appliance_id`` comes off the wire. JSON encoding already stops a forged
    newline splitting one record into two; the identifier alphabet stops the
    field being used as free text into the operator's log."""
    line = outcome_log_line(outcome="rejected", appliance_id=forged)
    assert "\n" not in line
    assert json.loads(line)["appliance_id"] is None


@pytest.mark.parametrize("seq", ["3", 3.0, True, None, {"a": 1}])
def test_a_non_integer_batch_seq_is_logged_as_absent(seq):
    assert json.loads(outcome_log_line(outcome="rejected", batch_seq=seq))["batch_seq"] is None


def test_the_tenant_never_reaches_the_line_even_as_the_reason():
    record = json.loads(outcome_log_line(
        outcome="rejected", reason="unauthorized_scope", appliance_id="a1"))
    assert "tenant" not in json.dumps(record).lower()


# ---------------------------------------------------------------------------
# Part B2 -- /metrics carries rejections, duplicates, outages and the chain tip
# ---------------------------------------------------------------------------

def test_counters_tally_each_outcome_by_class():
    counters = OutcomeCounters()
    counters.record("h", "rejected", "content_boundary_violated:$.events[0].content")
    counters.record("h", "rejected", "content_boundary_violated:$.counts.by_action_class")
    counters.record("h", "rejected", "unknown_envelope_field")
    counters.record("h", "duplicate")
    counters.record("h", "unavailable")
    counters.record("h", "accepted")
    assert counters.snapshot("h") == {
        "rejected_total": {"content_boundary_violated": 2, "unknown_envelope_field": 1},
        "duplicates_total": 1, "unavailable_total": 1, "accepted_total": 1,
    }


def test_an_unseen_tenant_reads_zeroes_rather_than_raising():
    assert OutcomeCounters().snapshot("never-seen") == {
        "rejected_total": {}, "duplicates_total": 0,
        "unavailable_total": 0, "accepted_total": 0}


def test_counters_are_atomic_under_concurrent_requests():
    """ThreadingHTTPServer serves each request on its own thread, so every
    counter update is concurrent by construction.

    The tally is a read-modify-write over several bytecodes, so dropping the
    lock loses updates -- but only when the interpreter happens to switch inside
    that window, which at the default 5ms switch interval it almost never does.
    A test that does not force the window green-lights an unlocked counter, so
    the interval is squeezed for the duration and restored after."""
    counters = OutcomeCounters()
    original_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        def hammer():
            for _ in range(3000):
                counters.record("h", "rejected", "unknown_envelope_field")

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        sys.setswitchinterval(original_interval)
    assert counters.snapshot("h")["rejected_total"]["unknown_envelope_field"] == 24000


def test_a_snapshot_cannot_be_mutated_through():
    counters = OutcomeCounters()
    counters.record("h", "rejected", "unknown_envelope_field")
    counters.snapshot("h")["rejected_total"]["unknown_envelope_field"] = 999
    assert counters.snapshot("h")["rejected_total"]["unknown_envelope_field"] == 1


def two_tenant_store(tmp_path):
    a = Appliance(tmp_path / "a", "a1")
    b = Appliance(tmp_path / "b", "a2")
    store = ApplianceIngestStore(tmp_path / "t.sqlite3", MIGRATION,
                                 verify_batch=verify_batch, prohibited_fields=CONTENT_FIELDS)
    store.enroll(appliance_id="a1", tenant_id="tenantA", source_id="s",
                 token="tokA", public_key_pem=a.public_key_pem)
    store.enroll(appliance_id="a2", tenant_id="tenantB", source_id="s",
                 token="tokB", public_key_pem=b.public_key_pem)
    return store, a, b


def test_a_silent_tenant_and_a_wholly_rejected_tenant_no_longer_look_identical(tmp_path):
    """The actual gap the ruling named. tenantB delivered nothing; tenantA was
    refused on every attempt. Before this both returned all-zero counts, so the
    failure mode the pilot is most likely to hit was the one nothing surfaced."""
    store, a, _ = two_tenant_store(tmp_path)
    for _ in range(3):
        batch = a.emit(tenant="tenantA")
        batch["evidence_tier"] = "attested"
        with pytest.raises(IngestRejected):
            store.ingest(batch, token="tokA", tenant_id="tenantA", source_id="s")
        store.record_outcome(tenant_id="tenantA", outcome="rejected",
                             reason="unknown_envelope_field")

    busy = store.scoped_status(token="tokA", tenant_id="tenantA")
    silent = store.scoped_status(token="tokB", tenant_id="tenantB")
    assert busy["batches_accepted"] == silent["batches_accepted"] == 0
    assert busy["rejected_total"] == {"unknown_envelope_field": 3}
    assert silent["rejected_total"] == {}
    assert busy != silent


def test_metrics_counters_do_not_cross_tenants(tmp_path):
    store, _, _ = two_tenant_store(tmp_path)
    store.record_outcome(tenant_id="tenantA", outcome="rejected", reason="unauthorized_scope")
    store.record_outcome(tenant_id="tenantB", outcome="duplicate")
    assert store.scoped_status(token="tokA", tenant_id="tenantA") == {
        **store.scoped_status(token="tokA", tenant_id="tenantA"),
        "rejected_total": {"unauthorized_scope": 1}, "duplicates_total": 0}
    assert store.scoped_status(token="tokB", tenant_id="tenantB")["rejected_total"] == {}
    assert store.scoped_status(token="tokB", tenant_id="tenantB")["duplicates_total"] == 1


def test_the_extended_metrics_keep_the_f5_scoping(tmp_path):
    """The surface grew; the control on it did not move."""
    store, _, _ = two_tenant_store(tmp_path)
    store.record_outcome(tenant_id="tenantA", outcome="rejected", reason="unknown_envelope_field")
    for token, tenant in [("", "tenantA"), ("tokB", "tenantA"), ("tokA", "tenantB")]:
        with pytest.raises(IngestRejected, match="unauthorized_scope"):
            store.scoped_status(token=token, tenant_id=tenant)


def test_the_chain_tip_is_per_appliance_and_survives_a_restart(tmp_path):
    """Counters are process-local by design; the tip is not, so an operator can
    still tell a quiet appliance from a stalled one after the unit restarts."""
    store, a, b = two_tenant_store(tmp_path)
    for _ in range(2):
        store.ingest(a.emit(tenant="tenantA"), token="tokA", tenant_id="tenantA", source_id="s")
    store.ingest(b.emit(tenant="tenantB"), token="tokB", tenant_id="tenantB", source_id="s")

    assert store.scoped_status(token="tokA", tenant_id="tenantA")["chain_tip_batch_seq"] == {"a1": 2}
    assert store.scoped_status(token="tokB", tenant_id="tenantB")["chain_tip_batch_seq"] == {"a2": 1}

    restarted = ApplianceIngestStore(store.database, MIGRATION, verify_batch=verify_batch,
                                     prohibited_fields=CONTENT_FIELDS)
    after = restarted.scoped_status(token="tokA", tenant_id="tenantA")
    assert after["chain_tip_batch_seq"] == {"a1": 2}
    assert after["rejected_total"] == {}          # process-local, as documented


def test_a_tenant_with_no_receipts_reads_an_empty_chain_tip(tmp_path):
    store, _, _ = two_tenant_store(tmp_path)
    assert store.scoped_status(token="tokB", tenant_id="tenantB")["chain_tip_batch_seq"] == {}


def test_the_pre_existing_counts_are_still_carried(tmp_path):
    store, a, _ = two_tenant_store(tmp_path)
    store.ingest(a.emit(tenant="tenantA", count=3), token="tokA",
                 tenant_id="tenantA", source_id="s")
    scoped = store.scoped_status(token="tokA", tenant_id="tenantA")
    assert scoped["batches_accepted"] == 1 and scoped["events_accepted"] == 3
    assert json.loads(json.dumps(scoped))          # the whole surface is wire-serialisable


# ---------------------------------------------------------------------------
# Part B -- end to end over the real HTTP boundary
# ---------------------------------------------------------------------------

class Receiver:
    """The server as the unit runs it, on a loopback port, with stderr captured."""

    def __init__(self, store: ApplianceIngestStore) -> None:
        self.store = store
        self.log = io.StringIO()
        self.httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(store, log_stream=self.log))
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def post(self, payload, token="tokA", tenant="tenantA", source="s"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/ingest", data=body, method="POST",
            headers={"Authorization": f"Bearer {token}", "X-BackBond-Tenant": tenant,
                     "X-BackBond-Source": source, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def metrics(self, token="tokA", tenant="tenantA"):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/metrics",
            headers={"Authorization": f"Bearer {token}", "X-BackBond-Tenant": tenant})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def lines(self, expected: int | None = None) -> list[dict]:
        """The outcome line is written after the response is flushed, so a client
        can legitimately observe the response first. Poll rather than race."""
        deadline = time.monotonic() + 5
        while True:
            found = [json.loads(line)
                     for line in self.log.getvalue().splitlines() if line.strip()]
            if expected is None or len(found) >= expected or time.monotonic() > deadline:
                return found
            time.sleep(0.01)


@pytest.fixture
def receiver(tmp_path):
    store, a, b = two_tenant_store(tmp_path)
    instance = Receiver(store)
    instance.appliance_a, instance.appliance_b = a, b
    try:
        yield instance
    finally:
        instance.close()


def test_each_request_outcome_writes_exactly_one_line(receiver):
    """journald carries stderr under the existing unit, so this is the whole
    delivery mechanism -- no new sink, no new dependency."""
    genuine = receiver.appliance_a.emit(tenant="tenantA")
    assert receiver.post(genuine)[0] == 200
    assert receiver.post(copy.deepcopy(genuine))[0] == 200        # duplicate
    rider = receiver.appliance_a.emit(tenant="tenantA")
    rider["evidence_tier"] = "attested"
    assert receiver.post(rider)[0] == 422

    lines = receiver.lines(3)
    assert [line["outcome"] for line in lines] == ["accepted", "duplicate", "rejected"]
    assert [line["reason_class"] for line in lines] == [None, None, "unknown_envelope_field"]
    assert {line["appliance_id"] for line in lines} == {"a1"}
    assert [line["batch_seq"] for line in lines] == [1, 1, 2]
    for line in lines:
        assert line["event"] == "appliance_ingest"
        datetime.strptime(line["ts"], "%Y-%m-%dT%H:%M:%S.%fZ")


def test_the_access_log_stays_silenced(receiver):
    """The default one-line-per-request access log is replaced, not joined."""
    receiver.post(receiver.appliance_a.emit(tenant="tenantA"))
    assert len(receiver.lines(1)) == 1
    assert "GET" not in receiver.log.getvalue() and "POST" not in receiver.log.getvalue()


def test_a_content_leak_is_logged_as_a_class_and_never_as_a_path(receiver):
    batch = receiver.appliance_a.emit(tenant="tenantA")
    batch["counts.by_action_class"] = {"message": "LEAKED CUSTOMER TEXT"}
    status, payload = receiver.post(batch)
    assert status == 422 and payload["error"].startswith("content_boundary_violated:")
    line, = receiver.lines(1)
    assert line["reason_class"] == "content_boundary_violated"
    assert "LEAKED" not in receiver.log.getvalue()
    assert "by_action_class" not in receiver.log.getvalue()


def test_the_tenant_header_never_reaches_the_log(receiver):
    receiver.post(receiver.appliance_a.emit(tenant="tenantA"), tenant="tenantA")
    assert receiver.lines(1)
    assert "tenantA" not in receiver.log.getvalue()


@pytest.mark.parametrize("payload", [b"[1,2,3]", b'"a string"', b"17"])
def test_a_non_object_body_is_a_deterministic_422_not_a_retryable_503(receiver, payload):
    """Without this it reached ``batch.get`` and died in the catch-all, so a
    permanently malformed body was answered ``503 persistence_unavailable`` --
    telling the sender to retry forever and inflating unavailable_total, the
    counter an operator would page on."""
    status, body = receiver.post(payload)
    assert status == 422 and body["error"] == "malformed_body"
    line, = receiver.lines(1)
    assert line["outcome"] == "rejected" and line["reason_class"] == "malformed_body"
    assert receiver.metrics()[1]["unavailable_total"] == 0


def test_undecodable_json_is_classified_without_echoing_the_decoder(receiver):
    status, _ = receiver.post(b"{not json")
    assert status == 422
    line, = receiver.lines(1)
    assert line["outcome"] == "rejected" and line["reason_class"] == "malformed_body"


def test_a_persistence_outage_is_the_only_thing_that_counts_as_unavailable(receiver, monkeypatch):
    original = receiver.store._connect

    class FailingConnection:
        def __init__(self, wrapped): self.wrapped = wrapped
        def __getattr__(self, name): return getattr(self.wrapped, name)
        def execute(self, sql, params=()):
            if sql.startswith("INSERT INTO appliance_ingest_receipts"):
                raise sqlite3.OperationalError("synthetic outage")
            return self.wrapped.execute(sql, params)
        def __enter__(self): return self
        def __exit__(self, *args): self.wrapped.close()

    monkeypatch.setattr(receiver.store, "_connect", lambda: FailingConnection(original()))
    status, body = receiver.post(receiver.appliance_a.emit(tenant="tenantA"))
    assert status == 503 and body["error"] == "persistence_unavailable"
    line, = receiver.lines(1)
    assert line["outcome"] == "unavailable" and line["reason_class"] == "persistence_unavailable"
    assert "synthetic outage" not in receiver.log.getvalue()

    monkeypatch.setattr(receiver.store, "_connect", original)
    assert receiver.metrics()[1]["unavailable_total"] == 1


def test_metrics_over_the_wire_reflects_what_the_wire_did(receiver):
    accepted = receiver.appliance_a.emit(tenant="tenantA")
    receiver.post(accepted)
    receiver.post(copy.deepcopy(accepted))
    rider = receiver.appliance_a.emit(tenant="tenantA")
    rider["evidence_tier"] = "attested"
    receiver.post(rider)
    receiver.post(receiver.appliance_a.emit(tenant="tenantA"), token="wrong")

    status, metrics = receiver.metrics()
    assert status == 200
    assert metrics["batches_accepted"] == 1
    assert metrics["duplicates_total"] == 1
    assert metrics["accepted_total"] == 1
    assert metrics["rejected_total"] == {"unknown_envelope_field": 1, "unauthorized_scope": 1}
    assert metrics["chain_tip_batch_seq"] == {"a1": 1}
    assert metrics["unavailable_total"] == 0


def test_metrics_is_still_refused_across_tenants_and_without_a_token(receiver):
    receiver.post(receiver.appliance_a.emit(tenant="tenantA"))
    assert receiver.metrics(token="tokB", tenant="tenantA")[0] == 403
    assert receiver.metrics(token="", tenant="tenantA")[0] == 403
    assert receiver.metrics(token="tokA", tenant="tenantB")[0] == 403
    assert receiver.metrics(token="tokB", tenant="tenantB")[1]["chain_tip_batch_seq"] == {}


def test_healthz_is_unchanged(receiver):
    request = urllib.request.Request(f"http://127.0.0.1:{receiver.port}/healthz")
    with urllib.request.urlopen(request) as response:
        assert response.status == 200 and json.loads(response.read()) == {"ok": True}


def test_the_server_emits_through_the_shared_writer(monkeypatch):
    """`emit_outcome` is the one place a line is written, so the redaction rules
    above cannot be bypassed by a second call site."""
    buffer = io.StringIO()
    server_module.emit_outcome(stream=buffer, outcome="rejected",
                               reason="malformed_counts:tokens", appliance_id="a1")
    record = json.loads(buffer.getvalue())
    assert record["reason_class"] == "malformed_counts" and record["appliance_id"] == "a1"
