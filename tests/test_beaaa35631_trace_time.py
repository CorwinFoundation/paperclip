"""BEAAA-35631: the mandate-to-outcome trace has to carry time, not just content.

Build specification (BEAAA-34387) line 218: "Store occurrence, receipt and
processing timestamps separately." Section 10: "a side-by-side view of
requested, approved and executed details, with time-ordered changes."

Before this change `PRAGMA table_info(cases)` and `PRAGMA table_info(executions)`
returned no timestamp column of any kind, and the only instant reachable in a
packet was `completed_at` inside an unparsed receipt JSON string. These tests
fail against that build.
"""

from __future__ import annotations

import re

import pytest

from stage_a_demo.approval import approve_mandate
from stage_a_demo.core import EvidenceStore, Mandate, SyntheticMerchantAdapter

INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z")


class SilentMerchant(SyntheticMerchantAdapter):
    """A merchant that returns no record, so no evidence ever arrives."""

    def purchase(self, **kwargs):
        return None


@pytest.fixture()
def store(tmp_path):
    store = EvidenceStore(tmp_path / "trace.db")
    try:
        yield store
    finally:
        store.close()


def _case(store, case_id, *, merchant=None, independent_evidence=True):
    mandate = Mandate(f"m-{case_id}", "SKU-1", 100)
    grant = approve_mandate(store, case_id=case_id, mandate=mandate)
    return store.record_case(
        case_id=case_id,
        grant_id=grant,
        asserted_sku="SKU-1",
        asserted_amount_cents=100,
        idempotency_key=case_id,
        merchant=merchant or SyntheticMerchantAdapter(),
        independent_evidence=independent_evidence,
    )


def test_schema_carries_time_bearing_columns(store):
    cases = {row[1] for row in store.db.execute("PRAGMA table_info(cases)")}
    executions = {row[1] for row in store.db.execute("PRAGMA table_info(executions)")}
    assert "recorded_at" in cases
    assert {"reserved_at", "occurred_at", "evidence_received_at", "recorded_at"} <= executions


def test_occurrence_receipt_and_processing_are_stored_separately(store):
    _case(store, "sep")
    row = store.db.execute(
        "SELECT reserved_at, occurred_at, evidence_received_at, recorded_at FROM executions"
    ).fetchone()
    for column in ("reserved_at", "occurred_at", "evidence_received_at", "recorded_at"):
        assert INSTANT.fullmatch(row[column]), (column, row[column])
    # Separate columns, not one value copied four ways.
    assert len({row[c] for c in ("reserved_at", "occurred_at", "evidence_received_at", "recorded_at")}) >= 3


def test_packet_trace_orders_approval_task_evidence_and_write(store):
    packet = _case(store, "ord")
    trace = packet["trace"]
    assert trace["unordered"] == []
    sequence = [entry["event"] for entry in trace["ordered"]]
    assert sequence.index("approved_at") < sequence.index("reserved_at")
    assert sequence.index("reserved_at") < sequence.index("occurred_at")
    assert sequence.index("occurred_at") < sequence.index("evidence_received_at")
    assert sequence.index("evidence_received_at") < sequence.index("recorded_at")
    stamps = [entry["at"] for entry in trace["ordered"]]
    assert stamps == sorted(stamps)


def test_three_distinct_instants_are_reachable_in_a_packet(store):
    packet = _case(store, "three")
    reachable = {
        packet["trace"]["approved_at"],
        packet["trace"]["occurred_at"],
        packet["trace"]["evidence_received_at"],
        packet["trace"]["recorded_at"],
        packet["execution"]["occurred_at"],
    }
    assert len({stamp for stamp in reachable if stamp}) >= 3


def test_a_case_with_no_independent_record_has_no_receipt_time(store):
    packet = _case(store, "silent", merchant=SilentMerchant())
    assert packet["sources"]["independent_merchant_evidence"] is None
    assert packet["trace"]["evidence_received_at"] is None
    assert packet["trace"]["unordered"] == ["evidence_received_at"]
    assert packet["trace"]["occurred_at"] is not None


def test_replay_returns_the_instants_of_the_first_execution(store):
    first = _case(store, "replay")
    again = store.record_case(
        case_id="replay",
        grant_id="unused",
        asserted_sku="SKU-1",
        asserted_amount_cents=100,
        idempotency_key="replay",
        merchant=SyntheticMerchantAdapter(),
    )
    assert again["trace"] == first["trace"]


def test_a_database_written_before_this_change_is_migrated(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """
        CREATE TABLE cases (
          case_id TEXT PRIMARY KEY, mode TEXT NOT NULL, mandate_json TEXT NOT NULL,
          approval_json TEXT NOT NULL, assertion_json TEXT NOT NULL,
          merchant_json TEXT, status TEXT NOT NULL, mismatch_json TEXT NOT NULL
        );
        CREATE TABLE approvals (
          grant_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, mandate_json TEXT NOT NULL,
          approval_token TEXT NOT NULL, origin TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE executions (
          id INTEGER PRIMARY KEY AUTOINCREMENT, idempotency_key TEXT UNIQUE NOT NULL,
          case_id TEXT NOT NULL, ledger_allocation TEXT UNIQUE NOT NULL,
          receipt_json TEXT NOT NULL
        );
        """
    )
    legacy.commit()
    legacy.close()

    store = EvidenceStore(path)
    try:
        packet = _case(store, "migrated")
        assert packet["trace"]["unordered"] == []
    finally:
        store.close()
