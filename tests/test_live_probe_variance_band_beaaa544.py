"""BEAAA-544 (T5) — live-probe variance band protocol.

Rule-change per `contracts/scanner-implementation-supervision-v1.md@v1.0`.
``cto_cosign_ref``: BEAAA-531 (CTO ACCEPT) + BEAAA-535 (HoUW countersign on
v1.1.0 contract). Patch text on BEAAA-531 document-contract-v1-1-0-patch.

AC mapping:

* AC#1 — K-replicate wired; K defaults to 5; allow-list = {5, 10}.
* AC#2 — Median aggregation (odd-K and even-K=10 lower-of-two-middles).
* AC#3 — ``spread_per_dim`` reported on every dim.
* AC#4 — Band check: spread ≤ 2 → ``incomplete: false``; spread > 2 on any
  pillar OR total → ``incomplete: true`` with partial result attached.
* AC#5 — UW payload carries ``live_probe_variance_band: 2``.
* AC#6 — Live-probe golden set N=5 within band (regression suite).
* AC#7 — Offline golden N=5 Δ = 0.0000 (regression suite — offline path
  not imported by the orchestrator).
* AC#8 — bin/are-scan + cli_runner.py zero diff (verified at parent close).
"""
from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from harness.credentials import CredentialVault
from harness.runner import MockRunner
from scanners.live_probe_orchestrator import (
    ALLOWED_K_REPLICATES,
    DEFAULT_K_REPLICATES,
    LIVE_PROBE_VARIANCE_BAND,
    InvalidKReplicatesError,
    ScanRequest,
    run_scan,
)
from scanners.live_probe_workers import END_OF_SCAN, WorkerChannel
from scanners.snapshot_hash import compute_corpus_snapshot_hash

SECRET = "SECRET_TEST_DO_NOT_LEAK_a3f9"
CRED_REF = "phase1-probe.v1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


def _request(corpus_hash: str, *, k_replicates: int = DEFAULT_K_REPLICATES) -> ScanRequest:
    return ScanRequest(
        scan_id="t5-scan",
        tenant_id="tenant-001",
        agent_endpoint="https://agent.example.com/api",
        credential_ref=CRED_REF,
        corpus_snapshot_hash=corpus_hash,
        scanner_version="0.3.0",
        corpus_version="0.1.0",
        seed=42,
        scan_mode="live_probe",
        k_replicates=k_replicates,
    )


def _runner() -> MockRunner:
    return MockRunner("t5", responder=lambda p: "ok")


def _per_replicate_probe_run_fn(raws_per_replicate: list[dict[str, float]]):
    """Build a probe_run_fn that yields rows whose ``raw`` varies across
    replicates. Each call to the function returns the next replicate's
    rows (the orchestrator calls probe_run_fn ``k_replicates`` times).
    """
    call_idx = {"i": 0}

    def fn(_runner):
        idx = call_idx["i"]
        rows = raws_per_replicate[idx]
        call_idx["i"] += 1
        for dim_id, raw in rows.items():
            yield dim_id, {
                "raw": float(raw),
                "evidence_uri": f"https://evidence.local/{dim_id}-r{idx}",
                "evidence_sha256": "sha256:" + "a" * 64,
            }

    return fn


# ---------------------------------------------------------------------------
# AC#1 — K-replicate default and allow-list.
# ---------------------------------------------------------------------------


def test_ac1_default_k_replicates_is_5():
    assert DEFAULT_K_REPLICATES == 5


def test_ac1_allowed_k_set_is_5_or_10():
    assert ALLOWED_K_REPLICATES == frozenset({5, 10})


def test_ac1_invalid_k_rejected_by_scan_request(corpus_hash):
    with pytest.raises(InvalidKReplicatesError, match="not in"):
        _request(corpus_hash, k_replicates=7)


def test_ac1_k_replicates_runs_probe_fn_n_times(corpus_tarball, corpus_hash, loaded_vault):
    """K=5 → probe_run_fn invoked 5 times → 5 raws per dim."""
    rows = [{"P1.1": 3.0, "P2.1": 2.5}] * 5
    channel = WorkerChannel(max_queue=128)
    result = run_scan(
        _request(corpus_hash, k_replicates=5),
        loaded_vault,
        channel,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=_per_replicate_probe_run_fn(rows),
        runner=_runner(),
    )
    p11 = next(d for d in result.dim_results if d.dim_id == "P1.1")
    assert len(p11.replicates) == 5


# ---------------------------------------------------------------------------
# AC#2 — median aggregation.
# ---------------------------------------------------------------------------


def test_ac2_odd_k_median_is_middle_value(corpus_tarball, corpus_hash, loaded_vault):
    """raws [3.0, 3.0, 4.0, 5.0, 5.0] → median 4.0, spread 2.0."""
    rows = [
        {"P1.1": 3.0},
        {"P1.1": 3.0},
        {"P1.1": 4.0},
        {"P1.1": 5.0},
        {"P1.1": 5.0},
    ]
    channel = WorkerChannel(max_queue=128)
    result = run_scan(
        _request(corpus_hash, k_replicates=5),
        loaded_vault,
        channel,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=_per_replicate_probe_run_fn(rows),
        runner=_runner(),
    )
    p11 = next(d for d in result.dim_results if d.dim_id == "P1.1")
    assert p11.raw == pytest.approx(4.0)
    assert p11.spread == pytest.approx(2.0)


def test_ac2_even_k10_uses_lower_of_two_middles(corpus_tarball, corpus_hash, loaded_vault):
    """K=10 raws [1..10] → middles are 5 and 6; lower = 5."""
    rows = [{"P1.1": float(i + 1)} for i in range(10)]
    channel = WorkerChannel(max_queue=128)
    result = run_scan(
        _request(corpus_hash, k_replicates=10),
        loaded_vault,
        channel,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=_per_replicate_probe_run_fn(rows),
        runner=_runner(),
    )
    p11 = next(d for d in result.dim_results if d.dim_id == "P1.1")
    assert p11.raw == pytest.approx(5.0)
    assert p11.spread == pytest.approx(9.0)


# ---------------------------------------------------------------------------
# AC#3 — spread_per_dim reported on every dim.
# ---------------------------------------------------------------------------


def test_ac3_spread_per_dim_keys_match_emitted_dims(corpus_tarball, corpus_hash, loaded_vault):
    rows = [{"P1.1": 3.0, "P2.1": 2.5}] * 5
    channel = WorkerChannel(max_queue=128)
    result = run_scan(
        _request(corpus_hash, k_replicates=5),
        loaded_vault,
        channel,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=_per_replicate_probe_run_fn(rows),
        runner=_runner(),
    )
    assert set(result.spread_per_dim.keys()) == {"P1.1", "P2.1"}


# ---------------------------------------------------------------------------
# AC#4 — band check on spread.
# ---------------------------------------------------------------------------


def test_ac4_within_band_marks_incomplete_false(corpus_tarball, corpus_hash, loaded_vault):
    """Spread = 2.0 on P1.1, ≤ 0.5 on P2.1 → both within the ±2 band → complete."""
    rows = [
        {"P1.1": 3.0, "P2.1": 2.5},
        {"P1.1": 3.0, "P2.1": 3.0},
        {"P1.1": 4.0, "P2.1": 2.5},
        {"P1.1": 5.0, "P2.1": 3.0},
        {"P1.1": 5.0, "P2.1": 2.5},
    ]
    channel = WorkerChannel(max_queue=128)
    result = run_scan(
        _request(corpus_hash, k_replicates=5),
        loaded_vault,
        channel,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=_per_replicate_probe_run_fn(rows),
        runner=_runner(),
    )
    assert result.spread_per_dim["P1.1"] == pytest.approx(2.0)
    assert result.spread_per_dim["P2.1"] == pytest.approx(0.5)
    assert result.incomplete is False
    assert result.incomplete_reason is None
    assert result.status == "complete"


def test_ac4_total_spread_exceeded_marks_incomplete(corpus_tarball, corpus_hash, loaded_vault):
    """Spread = 3.0 on P1.1 (2..5) → exceeds ±2 band → incomplete with partial."""
    rows = [
        {"P1.1": 2.0, "P2.1": 2.5},
        {"P1.1": 3.0, "P2.1": 2.5},
        {"P1.1": 4.0, "P2.1": 2.5},
        {"P1.1": 5.0, "P2.1": 2.5},
        {"P1.1": 5.0, "P2.1": 2.5},
    ]
    channel = WorkerChannel(max_queue=128)
    result = run_scan(
        _request(corpus_hash, k_replicates=5),
        loaded_vault,
        channel,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=_per_replicate_probe_run_fn(rows),
        runner=_runner(),
    )
    assert result.spread_per_dim["P1.1"] == pytest.approx(3.0)
    assert result.incomplete is True
    assert result.incomplete_reason in (
        "variance_band_exceeded_total",
        "variance_band_exceeded_pillar",
    )
    assert result.status == "incomplete"
    # Partial result is intact: dim_results still populated.
    assert len(result.dim_results) == 2


def test_ac4_pillar_spread_exceeded_marks_incomplete(corpus_tarball, corpus_hash, loaded_vault):
    """A dim in P2 pillar with spread > 2 → ``incomplete_reason`` is the
    pillar form (or total, since the dim's spread is also the total max).
    """
    rows = [
        {"P1.1": 3.0, "P2.1": 1.0},
        {"P1.1": 3.0, "P2.1": 2.0},
        {"P1.1": 3.0, "P2.1": 3.0},
        {"P1.1": 3.0, "P2.1": 4.0},
        {"P1.1": 3.0, "P2.1": 5.0},
    ]
    channel = WorkerChannel(max_queue=128)
    result = run_scan(
        _request(corpus_hash, k_replicates=5),
        loaded_vault,
        channel,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=_per_replicate_probe_run_fn(rows),
        runner=_runner(),
    )
    assert result.pillar_spread["P2"] == pytest.approx(4.0)
    assert result.incomplete is True


# ---------------------------------------------------------------------------
# AC#5 — UW payload carries live_probe_variance_band.
# ---------------------------------------------------------------------------


def test_ac5_uw_payload_carries_variance_band(corpus_tarball, corpus_hash, loaded_vault):
    rows = [{"P1.1": 3.0}] * 5
    channel = WorkerChannel(max_queue=128)
    result = run_scan(
        _request(corpus_hash, k_replicates=5),
        loaded_vault,
        channel,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=_per_replicate_probe_run_fn(rows),
        runner=_runner(),
    )
    assert result.live_probe_variance_band == LIVE_PROBE_VARIANCE_BAND == 2


def test_ac5_scanresult_k_replicates_mirrors_request(corpus_tarball, corpus_hash, loaded_vault):
    """Reproducibility: ``k_replicates`` on the result is the same value
    used in the run (so the UW pricing engine sees exactly the band that
    was applied)."""
    rows = [{"P1.1": 3.0}] * 10
    channel = WorkerChannel(max_queue=128)
    result = run_scan(
        _request(corpus_hash, k_replicates=10),
        loaded_vault,
        channel,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=_per_replicate_probe_run_fn(rows),
        runner=_runner(),
    )
    assert result.k_replicates == 10


# ---------------------------------------------------------------------------
# Pillar rollup helper sanity — exercise the per-pillar spread surface.
# ---------------------------------------------------------------------------


def test_pillar_spread_keys_are_canonical(corpus_tarball, corpus_hash, loaded_vault):
    rows = [{"P1.1": 3.0, "P3.2": 4.0}] * 5
    channel = WorkerChannel(max_queue=128)
    result = run_scan(
        _request(corpus_hash, k_replicates=5),
        loaded_vault,
        channel,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=_per_replicate_probe_run_fn(rows),
        runner=_runner(),
    )
    assert set(result.pillar_spread.keys()) == {"P1", "P2", "P3", "P4"}


# ---------------------------------------------------------------------------
# Hard Invariant #2 — offline path remains unaffected.
# ---------------------------------------------------------------------------


def test_offline_replay_route_does_not_exercise_band_protocol():
    """Sentry: the orchestrator is only entered on ``scan_mode='live_probe'``.
    The offline replay path goes through ``api/handlers.score_handler``
    which does not import the orchestrator. Spot-check by importing the
    handler module and confirming the orchestrator is not transitively
    imported on a clean process.
    """
    import importlib
    import subprocess
    import sys

    script = (
        "import sys\n"
        "import api.handlers  # noqa: F401\n"
        "print('scanners.live_probe_orchestrator' in sys.modules)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "False", (
        "api.handlers transitively imports scanners.live_probe_orchestrator — "
        "Hard Invariant #2 (offline path unaffected) is at risk."
    )
