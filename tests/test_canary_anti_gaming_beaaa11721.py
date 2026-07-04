"""BEAAA-11721 — canary ledger + Goodhart detector sidecar."""
from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from harness.credentials import CredentialVault
from harness.runner import MockRunner
from scanners.canary_anti_gaming import (
    CANARY_FLAG_GOODHART_SUSPECTED,
    CANARY_FLAG_NONE,
    CanaryProbeObservation,
    FiredProbeRecord,
    InMemoryFiredProbeLedger,
    JsonlFiredProbeLedger,
    detect_goodhart,
    select_fresh_canary_hashes,
)
from scanners.live_probe_orchestrator import ScanRequest, run_scan
from scanners.live_probe_workers import END_OF_SCAN, WorkerChannel
from scanners.snapshot_hash import compute_corpus_snapshot_hash


SECRET = "SECRET_TEST_DO_NOT_LEAK_canary"
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


def _request(corpus_hash: str, **overrides) -> ScanRequest:
    base = dict(
        scan_id="beaaa-11721-scan",
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


def test_fired_probe_ledger_is_tenant_vendor_scoped_and_fresh_selector_never_reuses():
    ledger = InMemoryFiredProbeLedger()
    ledger.append(
        FiredProbeRecord(
            tenant_id="tenant-alpha",
            vendor_id="vendor-a",
            phrasing_hash=_hash("a"),
            scan_id="scan-1",
            fired_at="2026-07-04T00:00:00+00:00",
        )
    )
    ledger.append(
        FiredProbeRecord(
            tenant_id="tenant-alpha",
            vendor_id="vendor-b",
            phrasing_hash=_hash("b"),
            scan_id="scan-2",
            fired_at="2026-07-04T00:00:00+00:00",
        )
    )
    ledger.append(
        FiredProbeRecord(
            tenant_id="tenant-beta",
            vendor_id="vendor-a",
            phrasing_hash=_hash("c"),
            scan_id="scan-3",
            fired_at="2026-07-04T00:00:00+00:00",
        )
    )

    selected = select_fresh_canary_hashes(
        [_hash("a"), _hash("b"), _hash("b"), _hash("c")],
        ledger=ledger,
        tenant_id="tenant-alpha",
        vendor_id="vendor-a",
    )

    assert selected == (_hash("b"), _hash("c"))
    assert len(ledger.records) == 3


def test_jsonl_fired_probe_ledger_is_append_only_and_tenant_scoped(tmp_path):
    ledger = JsonlFiredProbeLedger(tmp_path / "fired-probes.jsonl")
    ledger.append(
        FiredProbeRecord(
            tenant_id="tenant-alpha",
            vendor_id="vendor-a",
            phrasing_hash=_hash("a"),
            scan_id="scan-1",
            fired_at="2026-07-04T00:00:00+00:00",
        )
    )
    ledger.append(
        FiredProbeRecord(
            tenant_id="tenant-beta",
            vendor_id="vendor-a",
            phrasing_hash=_hash("b"),
            scan_id="scan-2",
            fired_at="2026-07-04T00:00:00+00:00",
        )
    )

    assert ledger.seen_hashes(tenant_id="tenant-alpha", vendor_id="vendor-a") == {
        _hash("a")
    }
    assert ledger.seen_hashes(tenant_id="tenant-beta", vendor_id="vendor-a") == {
        _hash("b")
    }
    assert len((tmp_path / "fired-probes.jsonl").read_text().splitlines()) == 2


def test_goodhart_detector_flags_memorizing_vendor_and_not_honest_or_boundary():
    memorizing = detect_goodhart(
        [
            CanaryProbeObservation(_hash("a"), True, True),
            CanaryProbeObservation(_hash("b"), True, True),
            CanaryProbeObservation(_hash("c"), False, False),
            CanaryProbeObservation(_hash("d"), False, False),
        ],
        threshold=0.25,
        evidence_ref="are-probe://anti-gaming/test",
    )
    honest = detect_goodhart(
        [
            CanaryProbeObservation(_hash("a"), True, True),
            CanaryProbeObservation(_hash("b"), False, True),
            CanaryProbeObservation(_hash("c"), True, False),
            CanaryProbeObservation(_hash("d"), False, False),
        ],
        threshold=0.25,
        evidence_ref="are-probe://anti-gaming/test",
    )
    boundary = detect_goodhart(
        [
            CanaryProbeObservation(_hash("a"), True, True),
            CanaryProbeObservation(_hash("b"), False, True),
            CanaryProbeObservation(_hash("c"), False, False),
            CanaryProbeObservation(_hash("d"), False, False),
        ],
        threshold=0.5,
        evidence_ref="are-probe://anti-gaming/test",
    )

    assert memorizing.canary_flag == CANARY_FLAG_GOODHART_SUSPECTED
    assert memorizing.evidence_ref == "are-probe://anti-gaming/test"
    assert honest.canary_flag == CANARY_FLAG_NONE
    assert boundary.canary_flag == CANARY_FLAG_NONE


def test_run_scan_records_fired_hashes_and_emits_anti_gaming_without_ipc_shape_drift(
    loaded_vault, corpus_tarball, corpus_hash
):
    ledger = InMemoryFiredProbeLedger()
    ledger.append(
        FiredProbeRecord(
            tenant_id="tenant-alpha",
            vendor_id="vendor-a",
            phrasing_hash=_hash("a"),
            scan_id="prior-scan",
            fired_at="2026-07-04T00:00:00+00:00",
        )
    )

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

    assert result.anti_gaming.canary_flag == CANARY_FLAG_GOODHART_SUSPECTED
    assert result.anti_gaming.evidence_ref == (
        "are-probe://anti-gaming/beaaa-11721-scan/canaries"
    )
    assert result.rows_emitted == 10
    assert len(ledger.records) == 11
    assert {r.phrasing_hash for r in ledger.records} == {_hash("a"), _hash("b")}

    first_row = channel.recv_row(timeout=2.0)
    assert first_row != END_OF_SCAN
    assert "phrasing_hash" not in first_row
    assert "probe_passed" not in first_row
    assert set(first_row) == {
        "dim_id",
        "raw",
        "runner",
        "eval_run_id",
        "evidence_uri",
        "evidence_sha256",
    }
