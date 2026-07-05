"""BEAAA-11876 — JSONL->Postgres durable loader + hash-chain for the
fired-probe canary audit-spine ledger.

Follow-on to BEAAA-11872. Three surfaces:

* hash-chaining at the JSONL layer (prev_sha256 link over preimage_sha256), so a
  whole-line delete / reorder / edit is detectable at the runtime sink itself
  via ``AuditSpineFiredProbeLedger.verify_chain``;
* the JSONL->Postgres loader that drains the EFS sink into the durable
  append-only book of record, idempotently on ``chain_sha256``;
* reconciliation that raises a tamper alarm on whole-of-record divergence
  (durable rows missing from the JSONL / broken chain / duplicates).

The hash-chain must NOT disturb the 11872 preimage guarantee (envelope-excluded,
replay-stable) — a regression assertion covers that.
"""
from __future__ import annotations

import json
from pathlib import Path

from scanners.canary_anti_gaming import (
    FIRED_PROBE_CHAIN_GENESIS,
    AuditSpineFiredProbeLedger,
    FiredProbeRecord,
    _fired_probe_preimage_sha256,
)
from scanners.fired_probe_ledger_loader import (
    FiredProbeLedgerLoader,
    InMemoryFiredProbeRowSink,
    PostgresFiredProbeRowSink,
    _durable_row_from_entry,
)


def _hash(fill: str) -> str:
    return "sha256:" + fill * 64


def _record(**overrides) -> FiredProbeRecord:
    base = dict(
        tenant_id="tenant-alpha",
        vendor_id="vendor-a",
        phrasing_hash=_hash("a"),
        scan_id="scan-1",
        fired_at="2026-07-05T00:00:00+00:00",
        rubric_point_id="P1.1",
        test_id="t-1",
    )
    base.update(overrides)
    return FiredProbeRecord(**base)


def _ledger(tmp_path: Path, n: int = 0) -> AuditSpineFiredProbeLedger:
    ledger = AuditSpineFiredProbeLedger(
        tmp_path / "spine" / "fired_probe_ledger.jsonl",
        now_factory=lambda: "2026-07-05T09:00:00+00:00",
    )
    for i in range(n):
        ledger.append(_record(phrasing_hash=_hash("abcdef"[i % 6]), scan_id=f"scan-{i}"))
    return ledger


def _rewrite(ledger: AuditSpineFiredProbeLedger, rows) -> None:
    """Overwrite the JSONL sink with the given rows — simulates an EFS-write
    holder tampering with the whole-of-record file."""
    text = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        for row in rows
    )
    ledger._path().write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------
# 1. Hash-chain at the JSONL layer.
# --------------------------------------------------------------------------


def test_append_writes_chain_fields_first_links_from_genesis(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.append(_record())
    row = ledger.read_all()[0]
    assert row["prev_sha256"] == FIRED_PROBE_CHAIN_GENESIS
    assert row["chain_sha256"].startswith("sha256:")
    assert row["chain_sha256"] != FIRED_PROBE_CHAIN_GENESIS


def test_chain_links_across_records(tmp_path):
    ledger = _ledger(tmp_path, n=3)
    rows = ledger.read_all()
    assert rows[0]["prev_sha256"] == FIRED_PROBE_CHAIN_GENESIS
    # Each record's prev_sha256 is the prior record's chain_sha256.
    assert rows[1]["prev_sha256"] == rows[0]["chain_sha256"]
    assert rows[2]["prev_sha256"] == rows[1]["chain_sha256"]


def test_verify_chain_ok_on_clean_ledger(tmp_path):
    ledger = _ledger(tmp_path, n=4)
    verdict = ledger.verify_chain()
    assert verdict.ok is True
    assert verdict.head == ledger.read_all()[-1]["chain_sha256"]


def test_verify_chain_detects_midstream_delete(tmp_path):
    ledger = _ledger(tmp_path, n=3)
    rows = ledger.read_all()
    _rewrite(ledger, [rows[0], rows[2]])  # drop the middle line
    verdict = ledger.verify_chain()
    assert verdict.ok is False
    assert verdict.broken_index == 1
    assert verdict.reason == "prev_sha256_mismatch"


def test_verify_chain_detects_reorder(tmp_path):
    ledger = _ledger(tmp_path, n=3)
    rows = ledger.read_all()
    _rewrite(ledger, [rows[0], rows[2], rows[1]])  # swap last two
    verdict = ledger.verify_chain()
    assert verdict.ok is False
    assert verdict.broken_index == 1


def test_verify_chain_detects_inplace_edit(tmp_path):
    ledger = _ledger(tmp_path, n=2)
    rows = [dict(r) for r in ledger.read_all()]
    rows[0]["phrasing_hash"] = _hash("f")  # tamper a semantic field
    _rewrite(ledger, rows)
    verdict = ledger.verify_chain()
    assert verdict.ok is False
    assert verdict.broken_index == 0
    assert verdict.reason == "preimage_sha256_mismatch"


def test_chain_does_not_disturb_preimage_stability(tmp_path):
    """Regression on 11872: the preimage stays envelope-excluded + replay-stable
    even with the chain fields present."""
    rec = _record()
    early = AuditSpineFiredProbeLedger(
        tmp_path / "a.jsonl", now_factory=lambda: "2026-01-01T00:00:00+00:00"
    )
    late = AuditSpineFiredProbeLedger(
        tmp_path / "b.jsonl", now_factory=lambda: "2026-12-31T23:59:59+00:00"
    )
    early.append(rec)
    late.append(rec)
    a, b = early.read_all()[0], late.read_all()[0]
    assert a["preimage_sha256"] == b["preimage_sha256"] == _fired_probe_preimage_sha256(rec)
    # Two fresh single-record ledgers -> identical chain (both link from genesis).
    assert a["chain_sha256"] == b["chain_sha256"]


# --------------------------------------------------------------------------
# 2. JSONL->Postgres loader — drain + idempotency.
# --------------------------------------------------------------------------


def test_loader_drains_all_records(tmp_path):
    ledger = _ledger(tmp_path, n=3)
    sink = InMemoryFiredProbeRowSink()
    report = FiredProbeLedgerLoader(ledger=ledger, sink=sink).load()
    assert report.scanned == 3
    assert report.inserted == 3
    assert report.already_present == 0
    assert len(sink.rows) == 3
    # Durable rows carry the chain lineage verbatim.
    assert {r["chain_sha256"] for r in sink.rows} == {
        row["chain_sha256"] for row in ledger.read_all()
    }


def test_loader_is_idempotent(tmp_path):
    ledger = _ledger(tmp_path, n=3)
    sink = InMemoryFiredProbeRowSink()
    loader = FiredProbeLedgerLoader(ledger=ledger, sink=sink)
    loader.load()
    second = loader.load()
    assert second.inserted == 0
    assert second.already_present == 3
    assert len(sink.rows) == 3


def test_loader_incremental_drain(tmp_path):
    ledger = _ledger(tmp_path, n=2)
    sink = InMemoryFiredProbeRowSink()
    loader = FiredProbeLedgerLoader(ledger=ledger, sink=sink)
    loader.load()
    ledger.append(_record(scan_id="scan-late", phrasing_hash=_hash("e")))
    report = loader.load()
    assert report.inserted == 1
    assert report.already_present == 2
    assert len(sink.rows) == 3


def test_loader_skips_malformed_and_legacy_lines(tmp_path):
    ledger = _ledger(tmp_path, n=1)
    rows = list(ledger.read_all())
    legacy = {k: v for k, v in rows[0].items() if k not in ("prev_sha256", "chain_sha256")}
    _rewrite(ledger, [rows[0], legacy, {"record_kind": "other", "chain_sha256": _hash("9")}])
    sink = InMemoryFiredProbeRowSink()
    report = FiredProbeLedgerLoader(ledger=ledger, sink=sink).load()
    assert report.scanned == 1
    assert report.inserted == 1
    assert report.skipped_malformed == 2


# --------------------------------------------------------------------------
# 3. Reconciliation — tamper gap analysis.
# --------------------------------------------------------------------------


def test_reconcile_clean_no_alarm(tmp_path):
    ledger = _ledger(tmp_path, n=3)
    sink = InMemoryFiredProbeRowSink()
    loader = FiredProbeLedgerLoader(ledger=ledger, sink=sink)
    loader.load()
    recon = loader.reconcile()
    assert recon.tamper_alarm is False
    assert recon.missing_in_postgres == ()
    assert recon.missing_in_jsonl == ()
    assert recon.chain_verification.ok is True


def test_reconcile_pre_drain_backlog_is_not_tamper(tmp_path):
    ledger = _ledger(tmp_path, n=3)
    sink = InMemoryFiredProbeRowSink()
    loader = FiredProbeLedgerLoader(ledger=ledger, sink=sink)
    # Not loaded yet -> JSONL ahead of Postgres. Backlog, not tamper.
    recon = loader.reconcile()
    assert len(recon.missing_in_postgres) == 3
    assert recon.missing_in_jsonl == ()
    assert recon.tamper_alarm is False


def test_reconcile_durable_row_missing_from_jsonl_is_tamper(tmp_path):
    ledger = _ledger(tmp_path, n=3)
    sink = InMemoryFiredProbeRowSink()
    loader = FiredProbeLedgerLoader(ledger=ledger, sink=sink)
    loader.load()
    rows = list(ledger.read_all())
    _rewrite(ledger, rows[:2])  # tail-truncate one loaded record from the sink
    recon = loader.reconcile()
    assert recon.tamper_alarm is True
    assert len(recon.missing_in_jsonl) == 1
    assert rows[2]["chain_sha256"] in recon.missing_in_jsonl


def test_reconcile_broken_chain_is_tamper(tmp_path):
    ledger = _ledger(tmp_path, n=3)
    sink = InMemoryFiredProbeRowSink()
    loader = FiredProbeLedgerLoader(ledger=ledger, sink=sink)
    loader.load()
    rows = list(ledger.read_all())
    _rewrite(ledger, [rows[0], rows[2]])  # mid-stream delete (still in PG)
    recon = loader.reconcile()
    assert recon.tamper_alarm is True
    assert recon.chain_verification.ok is False


def test_reconcile_duplicate_in_jsonl_is_tamper(tmp_path):
    ledger = _ledger(tmp_path, n=2)
    sink = InMemoryFiredProbeRowSink()
    loader = FiredProbeLedgerLoader(ledger=ledger, sink=sink)
    rows = list(ledger.read_all())
    _rewrite(ledger, [rows[0], rows[1], rows[1]])  # replayed line
    recon = loader.reconcile()
    assert recon.tamper_alarm is True
    assert rows[1]["chain_sha256"] in recon.duplicate_in_jsonl


# --------------------------------------------------------------------------
# 4. Postgres sink binding — SQL shape + append-only/idempotent contract.
# --------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, store):
        self._store = store
        self.rowcount = 0
        self._fetch = []

    def execute(self, sql, params=None):
        if sql.strip().startswith("SELECT"):
            self._fetch = [(c,) for c in self._store]
        else:  # INSERT ... ON CONFLICT DO NOTHING
            chain = params["chain_sha256"]
            if chain in self._store:
                self.rowcount = 0
            else:
                self._store.add(chain)
                self.rowcount = 1

    def fetchall(self):
        return self._fetch

    def close(self):
        pass


class _FakeConnection:
    def __init__(self):
        self.store = set()

    def cursor(self):
        return _FakeCursor(self.store)


def test_postgres_sink_insert_and_dedup(tmp_path):
    ledger = _ledger(tmp_path, n=3)
    conn = _FakeConnection()
    sink = PostgresFiredProbeRowSink(conn)
    loader = FiredProbeLedgerLoader(ledger=ledger, sink=sink)
    first = loader.load()
    assert first.inserted == 3
    # Idempotent against the durable set via ON CONFLICT DO NOTHING.
    second = loader.load()
    assert second.inserted == 0
    assert sink.chain_sha256s() == {r["chain_sha256"] for r in ledger.read_all()}


def test_durable_row_projection_rejects_non_fired_probe():
    assert _durable_row_from_entry({"record_kind": "other"}) is None
    assert _durable_row_from_entry({"record_kind": "fired_probe"}) is None
