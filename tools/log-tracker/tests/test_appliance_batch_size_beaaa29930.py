"""BEAAA-29930: batches must be bounded by serialized size, not only event count.

The receiver caps an ingest body at ``MAX_BODY_BYTES`` *after* gunzip and answers
an oversized one with a terminal 422 ``body_size_invalid``. The shipper treats a
non-429 4xx as fatal, so it raises before ``BatchBuilder.commit()`` and before the
reader commits its checkpoint -- the same window is then re-read on every timer
tick, forever. A window that satisfies ``max_events_per_batch`` is therefore not
enough on its own; it also has to fit the receiver's body cap.
"""
import sys
from pathlib import Path

import pytest

from log_tracker.appliance_ingest_server import MAX_BODY_BYTES

REPO = Path(__file__).resolve().parents[3]
APPLIANCE = REPO / "ops/paperclip/numbat/backbond-appliance-0.1.0/backbond-appliance"
INSTALLER = REPO / "ops/paperclip/numbat/install-listener-ariclaw.sh"

if str(APPLIANCE) not in sys.path:
    sys.path.insert(0, str(APPLIANCE))

batch_module = pytest.importorskip("appliance.batch")
BatchBuilder = batch_module.BatchBuilder
Signer = batch_module.Signer
DEFAULT_MAX_BATCH_BYTES = batch_module.DEFAULT_MAX_BATCH_BYTES
canonical = batch_module._canonical

PRODUCER = {"collector_version": "appliance-0.1.0", "evidence_profile_version": "1.0",
            "scanner_version": "na-appliance", "corpus_version": "na-appliance",
            "rule_pack_version": "0.1", "seed": 0}


def _builder(tmp_path):
    signer = Signer(str(tmp_path / "key.pem"), "test-key-1")
    return BatchBuilder("ariclaw-1", signer, str(tmp_path / "state"), PRODUCER)


def _events(count, payload_bytes):
    # `evidence_hash` is an emittable (non-content) field, so a large value here
    # models a real oversized window without smuggling content into the fixture.
    return [{"event_id": f"evt-{i}", "action_class": "tool_call", "billable": True,
             "evidence_hash": "a" * payload_bytes} for i in range(count)]


def test_window_splits_on_serialized_size_not_only_count(tmp_path):
    events = _events(40, 64 * 1024)                     # ~2.6 MiB, far under 5000 events
    batches = _builder(tmp_path).build(events, [], {"start": "t0", "end": "t1"},
                                       max_events=5000)
    assert len(batches) > 1, "a 2.6 MiB window stayed in one batch"
    for built in batches:
        assert len(canonical(built)) <= MAX_BODY_BYTES
    shipped = [e["event_id"] for built in batches for e in built["events"]]
    assert shipped == [e["event_id"] for e in events], "split lost or reordered events"


def test_split_batches_stay_chained_and_sequenced(tmp_path):
    batches = _builder(tmp_path).build(_events(40, 64 * 1024), [],
                                       {"start": "t0", "end": "t1"}, max_events=5000)
    assert [b["batch_seq"] for b in batches] == list(range(1, len(batches) + 1))
    assert batches[0]["prev_batch_hash"] is None
    for earlier, later in zip(batches, batches[1:]):
        assert later["prev_batch_hash"] == earlier["batch_hash"]


def test_an_event_larger_than_the_budget_is_still_emitted(tmp_path):
    batches = _builder(tmp_path).build(_events(2, DEFAULT_MAX_BATCH_BYTES + 1024), [],
                                       {"start": "t0", "end": "t1"}, max_events=5000)
    assert [len(b["events"]) for b in batches] == [1, 1], "oversized event was dropped"


def test_installed_config_bounds_the_live_window(tmp_path):
    config = INSTALLER.read_text()
    assert "max_events_per_batch: 1000" in config
    assert f"max_batch_bytes: {DEFAULT_MAX_BATCH_BYTES}" in config
    assert DEFAULT_MAX_BATCH_BYTES < MAX_BODY_BYTES
