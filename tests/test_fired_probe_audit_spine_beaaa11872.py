"""BEAAA-11872 — durable audit-spine-backed canary fired-probe ledger.

Covers Founder rule 3d: the fired-probe canary ledger must be wired to the
audit-spine implementation in production, not the in-memory/JSONL test ledgers
and not left ``None``. Three acceptance surfaces:

* ledger append on a fired probe in the live ``run_scan`` path,
* production-profile selection (walker active -> audit-spine, never None),
* replay / idempotency of the persisted book of record.
"""
from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from api import scan_runtime_factory as factory
from harness.credentials import CredentialVault
from harness.runner import MockRunner
from scanners.canary_anti_gaming import (
    FIRED_PROBE_AUDIT_SPINE_SCHEMA_VERSION,
    AuditSpineFiredProbeLedger,
    FiredProbeRecord,
    JsonlFiredProbeLedger,
    _fired_probe_preimage_sha256,
    select_fresh_canary_hashes,
)
from scanners.live_probe_orchestrator import ScanRequest, run_scan
from scanners.live_probe_workers import WorkerChannel
from scanners.snapshot_hash import compute_corpus_snapshot_hash


SECRET = "SECRET_TEST_DO_NOT_LEAK_canary11872"
CRED_REF = "phase1-probe.v1"


def _hash(fill: str) -> str:
    return "sha256:" + fill * 64


@pytest.fixture
def corpus_tarball(tmp_path: Path) -> Path:
    path = tmp_path / "corpus.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        data = b'{"corpus_version":"0.1.0"}\n'
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return path


@pytest.fixture
def corpus_hash(corpus_tarball: Path) -> str:
    return compute_corpus_snapshot_hash(corpus_tarball)


@pytest.fixture
def loaded_vault():
    vault = CredentialVault()
    vault.register(CRED_REF, SECRET)
    yield vault
    vault.forget_all()


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


def _request(corpus_hash: str, **overrides) -> ScanRequest:
    base = dict(
        scan_id="beaaa-11872-scan",
        tenant_id="tenant-alpha",
        vendor_id="vendor-a",
        agent_endpoint="https://agent.example.com/api",
        credential_ref=CRED_REF,
        corpus_snapshot_hash=corpus_hash,
        scanner_version="0.4.2",
        corpus_version="0.1.0",
        seed=42,
        k_replicates=5,
        canary_phrasing_hashes=(_hash("b"),),
    )
    base.update(overrides)
    return ScanRequest(**base)


# --------------------------------------------------------------------------
# 1. Direct append + audit-spine envelope + tenant/vendor scoping.
# --------------------------------------------------------------------------


def test_audit_spine_ledger_writes_versioned_tamper_evident_append_only_records(
    tmp_path,
):
    path = tmp_path / "spine" / "fired_probe_ledger.jsonl"
    ledger = AuditSpineFiredProbeLedger(path, now_factory=lambda: "2026-07-05T09:00:00+00:00")

    rec = _record()
    ledger.append(rec)
    ledger.append(_record(tenant_id="tenant-beta", phrasing_hash=_hash("b")))

    # Tenant/vendor scoped reads, drop-in with the in-mem/JSONL ledgers.
    assert ledger.seen_hashes(tenant_id="tenant-alpha", vendor_id="vendor-a") == {
        _hash("a")
    }
    assert ledger.seen_hashes(tenant_id="tenant-beta", vendor_id="vendor-a") == {
        _hash("b")
    }
    assert ledger.seen_hashes(tenant_id="tenant-gamma", vendor_id="vendor-a") == frozenset()

    rows = ledger.read_all()
    assert len(rows) == 2
    first = rows[0]
    # Audit-spine envelope present on every book-of-record entry.
    assert first["record_kind"] == "fired_probe"
    assert first["schema_version"] == FIRED_PROBE_AUDIT_SPINE_SCHEMA_VERSION
    assert first["recorded_at"] == "2026-07-05T09:00:00+00:00"
    assert first["preimage_sha256"] == _fired_probe_preimage_sha256(rec)
    # Semantic fields carried through unchanged.
    assert first["tenant_id"] == "tenant-alpha"
    assert first["vendor_id"] == "vendor-a"
    assert first["phrasing_hash"] == _hash("a")
    assert first["rubric_point_id"] == "P1.1"
    # Append-only: two appends -> two persisted lines.
    assert len(path.read_text().splitlines()) == 2


def test_audit_spine_preimage_excludes_envelope_and_is_replay_stable(tmp_path):
    """The tamper-evident preimage is stable regardless of recorded_at."""
    rec = _record()
    early = AuditSpineFiredProbeLedger(
        tmp_path / "a.jsonl", now_factory=lambda: "2026-01-01T00:00:00+00:00"
    )
    late = AuditSpineFiredProbeLedger(
        tmp_path / "b.jsonl", now_factory=lambda: "2026-12-31T23:59:59+00:00"
    )
    early.append(rec)
    late.append(rec)
    assert early.read_all()[0]["preimage_sha256"] == late.read_all()[0]["preimage_sha256"]


# --------------------------------------------------------------------------
# 2. Live path: append on fired probe through run_scan.
# --------------------------------------------------------------------------


def test_audit_spine_ledger_appends_on_fired_probe_in_live_scan_path(
    loaded_vault, corpus_tarball, corpus_hash, tmp_path
):
    path = tmp_path / "spine" / "fired_probe_ledger.jsonl"
    ledger = AuditSpineFiredProbeLedger(path)

    def probe_fn(_runner):
        yield "P1.1", {
            "raw": 4.0,
            "evidence_uri": "https://evidence.local/known",
            "evidence_sha256": _hash("d"),
            "phrasing_hash": _hash("a"),
            "probe_passed": True,
            "rubric_point_id": "P1.1",
            "test_id": "known-1",
        }
        yield "P1.1", {
            "raw": 0.0,
            "evidence_uri": "https://evidence.local/fresh",
            "evidence_sha256": _hash("e"),
            "phrasing_hash": _hash("b"),
            "probe_passed": False,
            "rubric_point_id": "P1.1",
            "test_id": "fresh-1",
        }

    channel = WorkerChannel(max_queue=256)
    result = run_scan(
        _request(corpus_hash),
        loaded_vault,
        channel,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=probe_fn,
        runner=MockRunner("canary", lambda p: "ok"),
        fired_probe_ledger=ledger,
    )

    assert result.status in ("complete", "incomplete")
    # Both fired phrasings landed in the durable audit spine, tenant/vendor scoped.
    assert ledger.seen_hashes(tenant_id="tenant-alpha", vendor_id="vendor-a") == {
        _hash("a"),
        _hash("b"),
    }
    rows = ledger.read_all()
    assert rows, "live path must persist at least one fired-probe record"
    assert all(r["record_kind"] == "fired_probe" for r in rows)
    assert all(r["scan_id"] == "beaaa-11872-scan" for r in rows)
    assert path.exists()


# --------------------------------------------------------------------------
# 3. Production-profile selection.
# --------------------------------------------------------------------------


def test_prod_profile_selects_audit_spine_ledger_when_walker_active(monkeypatch):
    monkeypatch.delenv(factory.ARE_FIRED_PROBE_AUDIT_SPINE_PATH_ENV, raising=False)
    monkeypatch.delenv(factory.ARE_FIRED_PROBE_LEDGER_PATH_ENV, raising=False)

    ledger = factory._select_fired_probe_ledger(walker_active=True)
    assert isinstance(ledger, AuditSpineFiredProbeLedger)
    # Never None, and defaults to the durable EFS audit-spine mount (not /tmp).
    assert str(ledger.path) == factory.ARE_FIRED_PROBE_AUDIT_SPINE_DEFAULT_PATH
    assert str(ledger.path).startswith("/var/lib/are-scanner/")


def test_prod_profile_audit_spine_path_override(monkeypatch, tmp_path):
    override = str(tmp_path / "override" / "ledger.jsonl")
    monkeypatch.setenv(factory.ARE_FIRED_PROBE_AUDIT_SPINE_PATH_ENV, override)
    ledger = factory._select_fired_probe_ledger(walker_active=True)
    assert isinstance(ledger, AuditSpineFiredProbeLedger)
    assert str(ledger.path) == override


def test_prod_profile_walker_off_is_legacy_behavior(monkeypatch, tmp_path):
    # Walker off + no legacy env -> None (byte-identical to pre-11872 default).
    monkeypatch.delenv(factory.ARE_FIRED_PROBE_LEDGER_PATH_ENV, raising=False)
    assert factory._select_fired_probe_ledger(walker_active=False) is None

    # Walker off + explicit legacy JSONL env -> JSONL escape hatch.
    legacy = str(tmp_path / "legacy.jsonl")
    monkeypatch.setenv(factory.ARE_FIRED_PROBE_LEDGER_PATH_ENV, legacy)
    ledger = factory._select_fired_probe_ledger(walker_active=False)
    assert isinstance(ledger, JsonlFiredProbeLedger)
    assert str(ledger.path) == legacy


# --------------------------------------------------------------------------
# 4. Replay / idempotency.
# --------------------------------------------------------------------------


def test_replay_reconstructs_seen_hashes_and_is_idempotent(tmp_path):
    path = tmp_path / "spine" / "fired_probe_ledger.jsonl"

    writer = AuditSpineFiredProbeLedger(path)
    writer.append(_record(phrasing_hash=_hash("a")))
    writer.append(_record(phrasing_hash=_hash("c"), scan_id="scan-2"))
    # Duplicate append: the book of record retains it (append-only), but
    # seen_hashes is idempotent (a set).
    writer.append(_record(phrasing_hash=_hash("a"), scan_id="scan-3"))

    # A fresh reader over the same durable file reconstructs the same view.
    reader = AuditSpineFiredProbeLedger(path)
    assert reader.seen_hashes(tenant_id="tenant-alpha", vendor_id="vendor-a") == {
        _hash("a"),
        _hash("c"),
    }
    # Append-only: three physical lines, duplicate retained.
    assert len(reader.read_all()) == 3

    # The fresh-canary selector sees the replayed history and never reuses.
    fresh = select_fresh_canary_hashes(
        [_hash("a"), _hash("c"), _hash("f")],
        ledger=reader,
        tenant_id="tenant-alpha",
        vendor_id="vendor-a",
    )
    assert fresh == (_hash("f"),)


def test_corrupt_trailing_line_is_tolerated_on_replay(tmp_path):
    path = tmp_path / "fired.jsonl"
    ledger = AuditSpineFiredProbeLedger(path)
    ledger.append(_record(phrasing_hash=_hash("a")))
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")
    assert ledger.seen_hashes(tenant_id="tenant-alpha", vendor_id="vendor-a") == {
        _hash("a")
    }
    assert len(ledger.read_all()) == 1


def test_audit_spine_line_is_canonical_json(tmp_path):
    path = tmp_path / "fired.jsonl"
    ledger = AuditSpineFiredProbeLedger(path, now_factory=lambda: "2026-07-05T00:00:00+00:00")
    ledger.append(_record())
    line = path.read_text().splitlines()[0]
    parsed = json.loads(line)
    # Canonical: sorted keys, tight separators.
    assert line == json.dumps(parsed, sort_keys=True, separators=(",", ":"))
