"""BEAAA-36242 count-only dimensional retrieval acceptance coverage."""
import copy
import json
import urllib.error
import urllib.request

import pytest

from log_tracker.appliance_ingest import IngestRejected
from test_appliance_ingest_p1_beaaa24725 import receiver  # noqa: F401


def query(receiver, payload, token="tokA", tenant="tenantA"):
    request = urllib.request.Request(
        f"http://127.0.0.1:{receiver.port}/counts",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "X-BackBond-Tenant": tenant,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


BOUNDS = {"occurrence": {"start": "2026-08-17T00:00:00Z",
                          "end": "2026-08-17T01:00:00Z"}}


def test_bounded_query_filters_all_dimensions_and_returns_aggregates_only(receiver):
    batch = receiver.appliance_a.emit(tenant="tenantA", count=2, etype="tool_call")
    assert receiver.post(batch, tenant="tenantA")[0] == 200
    status, result = query(receiver, {
        **BOUNDS, "agent_id": "growth-agent", "source_id": "s",
        "deployment_id": "BND-0001", "event_type": "tool_call", "outcome": "ok",
    })
    assert status == 200
    assert result["groups"] == [{
        "source_id_hash": result["groups"][0]["source_id_hash"],
        "deployment_id_hash": result["groups"][0]["deployment_id_hash"],
        "event_type": "tool_call", "outcome": "ok", "events_count": 2,
        "batches_count": 1, "first_batch_seq": 1, "last_batch_seq": 1,
    }]
    assert result["integrity_refs"] == [{
        "batch_hash": batch["batch_hash"], "appliance_id": "a1", "batch_seq": 1}]
    assert result["counters"] == {"missing": 0, "duplicate": 0, "rejected": 0,
                                   "misattributed": 0, "unavailable": 0}
    serialized = json.dumps(result)
    for forbidden in ("prompt", "response", "command", "tool_output", "record_body"):
        assert forbidden not in serialized


def test_query_requires_a_valid_bound_and_keeps_tenant_auth(receiver):
    assert query(receiver, {}) == (422, {"error": "bounded_window_required"})
    assert query(receiver, {"capture": {"start": "later", "end": "earlier"}}) == (
        422, {"error": "malformed_window"})
    assert query(receiver, BOUNDS, token="tokB", tenant="tenantA") == (
        403, {"error": "unauthorized_scope"})


def test_duplicate_rejected_and_misattributed_counters_are_explicit(receiver):
    batch = receiver.appliance_a.emit(tenant="tenantA")
    receiver.post(batch, tenant="tenantA")
    receiver.post(copy.deepcopy(batch), tenant="tenantA")
    receiver.post(receiver.appliance_a.emit(tenant="tenantA"),
                  token="wrong", tenant="tenantA")
    status, result = query(receiver, BOUNDS)
    assert status == 200
    assert result["counters"]["duplicate"] == 1
    assert result["counters"]["rejected"] == 1
    assert result["counters"]["misattributed"] == 1


def test_store_rejects_unbounded_direct_query(receiver):
    with pytest.raises(IngestRejected, match="bounded_window_required"):
        receiver.store.dimensional_counts(token="tokA", tenant_id="tenantA", query={})
