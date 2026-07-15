"""BEAAA-545 (T9) — per-scan operational ceilings (probe / token / wall-clock).

Rule-execution per `contracts/scanner-implementation-supervision-v1.md@v1.0`.
``cto_cosign_ref``: BEAAA-531 ACCEPT for the ``incomplete: true`` +
``incomplete_reason`` JSON shape only. Cap thresholds are implementation
defaults, not contract surface.

AC mapping:

* AC#1 — Three caps wired into the orchestrator's probe-emit loop.
* AC#2 — Probe cap halts with ``incomplete_reason='probe_cap'``.
* AC#3 — Token cap halts with ``incomplete_reason='token_cap'``.
* AC#4 — Wall-clock cap (mocked time) halts with ``incomplete_reason='wallclock_cap'``.
* AC#5 — Partial result is intact: all rows emitted before the cap reach the
  Scorer cleanly through the T4 IPC channel.
* AC#6 — Offline golden N=5 Δ = 0.0000 (regression suite — offline path
  not exercised by the cap logic).
* AC#7 — bin/are-scan + cli_runner.py zero diff (verified at parent close).
"""
from __future__ import annotations

import io
import tarfile
from itertools import count
from pathlib import Path

import pytest

from harness.credentials import CredentialVault
from harness.runner import MockRunner
from scanners.live_probe_orchestrator import (
    DEFAULT_K_REPLICATES,
    MAX_FULL_UNDERWRITING_PROBES_PER_SCAN,
    MAX_PROBES_PER_SCAN,
    MAX_SMOKE_DEV_PROBES_PER_SCAN,
    MAX_TOKENS_PER_SCAN,
    MAX_WALL_CLOCK_SECONDS,
    ProbeRunIncompleteError,
    ScanRequest,
    run_scan,
)
import scanners.live_probe_orchestrator as orch
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


def _request(
    corpus_hash: str,
    *,
    k_replicates: int = DEFAULT_K_REPLICATES,
    scan_tier: str = "full",
) -> ScanRequest:
    return ScanRequest(
        scan_id="t9-scan",
        tenant_id="tenant-001",
        agent_endpoint="https://agent.example.com/api",
        credential_ref=CRED_REF,
        corpus_snapshot_hash=corpus_hash,
        scanner_version="0.3.0",
        corpus_version="0.1.0",
        seed=42,
        scan_mode="live_probe",
        scan_tier=scan_tier,
        k_replicates=k_replicates,
    )


def _runner() -> MockRunner:
    return MockRunner("t9", responder=lambda p: "ok")


def _drain_channel(channel: WorkerChannel) -> list[dict]:
    items = []
    while True:
        msg = channel.recv_row(timeout=2.0)
        if msg == END_OF_SCAN:
            break
        items.append(msg)
    return items


# ---------------------------------------------------------------------------
# AC#1 — three cap constants exist with documented defaults.
# ---------------------------------------------------------------------------


def test_ac1_smoke_dev_probe_cap_default_is_1000():
    assert MAX_PROBES_PER_SCAN == 1000
    assert MAX_SMOKE_DEV_PROBES_PER_SCAN == 1000


def test_ac1_full_underwriting_probe_cap_is_not_1000():
    assert MAX_FULL_UNDERWRITING_PROBES_PER_SCAN > 1000


def test_ac1_token_cap_default_is_one_million():
    assert MAX_TOKENS_PER_SCAN == 1_000_000


def test_ac1_wallclock_cap_default_is_1800_seconds():
    assert MAX_WALL_CLOCK_SECONDS == 1800


# ---------------------------------------------------------------------------
# AC#2 — probe count cap.
# ---------------------------------------------------------------------------


def test_ac2_smoke_probe_cap_halts_at_default(monkeypatch, corpus_tarball, corpus_hash, loaded_vault):
    """Cap probe limit to a small value via monkeypatch — confirm the
    orchestrator emits exactly the smoke/dev cap rows then halts
    with ``incomplete_reason='probe_cap'``.
    """
    monkeypatch.setattr(orch, "MAX_SMOKE_DEV_PROBES_PER_SCAN", 3)

    def big_fn(_runner):
        # Generate plenty more rows than the cap.
        for i in range(50):
            yield f"P1.{(i % 9) + 1}", {
                "raw": float(i),
                "evidence_uri": f"https://evidence.local/probe-{i}",
                "evidence_sha256": "sha256:" + "a" * 64,
            }

    channel = WorkerChannel(max_queue=256)
    result = run_scan(
        _request(corpus_hash, scan_tier="smoke"),
        loaded_vault,
        channel,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=big_fn,
        runner=_runner(),
    )
    assert result.rows_emitted == 3
    assert result.incomplete is True
    assert result.incomplete_reason == "probe_cap"
    assert result.status == "incomplete"
    # AC#5 — every row crossed the IPC channel.
    drained = _drain_channel(channel)
    assert len(drained) == 3


def test_full_underwriting_attempts_more_than_1000_base_rows(
    monkeypatch, corpus_tarball, corpus_hash, loaded_vault
):
    monkeypatch.setattr(orch, "MAX_FULL_UNDERWRITING_PROBES_PER_SCAN", 10_000)

    def full_fn(_runner):
        for i in range(1005):
            yield f"P1.{(i % 9) + 1}", {
                "raw": 1.0,
                "evidence_uri": f"https://evidence.local/full-{i}",
                "evidence_sha256": "sha256:" + "8" * 64,
            }

    channel = WorkerChannel(max_queue=8192)
    result = run_scan(
        _request(corpus_hash, k_replicates=5, scan_tier="full"),
        loaded_vault,
        channel,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=full_fn,
        runner=_runner(),
    )

    assert result.rows_emitted == 1005 * 5
    assert result.incomplete_reason != "probe_cap"
    assert len(_drain_channel(channel)) == 1005 * 5


def test_full_underwriting_runaway_guard_refuses_instead_of_sampling(
    monkeypatch, corpus_tarball, corpus_hash, loaded_vault
):
    monkeypatch.setattr(orch, "MAX_FULL_UNDERWRITING_PROBES_PER_SCAN", 7)

    def runaway_fn(_runner):
        i = 0
        while True:
            yield f"P1.{(i % 9) + 1}", {
                "raw": 1.0,
                "evidence_uri": f"https://evidence.local/runaway-{i}",
                "evidence_sha256": "sha256:" + "9" * 64,
            }
            i += 1

    channel = WorkerChannel(max_queue=64)
    result = run_scan(
        _request(corpus_hash, scan_tier="full"),
        loaded_vault,
        channel,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=runaway_fn,
        runner=_runner(),
    )

    assert result.status == "incomplete"
    assert result.incomplete is True
    assert result.incomplete_reason == "probe_cap"
    assert result.rows_emitted == 7
    assert len(_drain_channel(channel)) == 7


# ---------------------------------------------------------------------------
# AC#3 — token cap.
# ---------------------------------------------------------------------------


def test_ac3_token_cap_halts_with_partial_result(monkeypatch, corpus_tarball, corpus_hash, loaded_vault):
    """Cap tokens to a small value; each probe yield reports its token
    count via the 3-tuple form. The orchestrator halts once the running
    sum exceeds the cap.
    """
    monkeypatch.setattr(orch, "MAX_TOKENS_PER_SCAN", 25)

    def token_fn(_runner):
        # 10 probes each at 10 tokens — token cap of 25 fires after
        # the third (running sum = 30 > 25).
        for i in range(20):
            yield (
                f"P1.{(i % 9) + 1}",
                {
                    "raw": 1.0,
                    "evidence_uri": f"https://evidence.local/tok-{i}",
                    "evidence_sha256": "sha256:" + "b" * 64,
                },
                10,
            )

    channel = WorkerChannel(max_queue=256)
    result = run_scan(
        _request(corpus_hash),
        loaded_vault,
        channel,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=token_fn,
        runner=_runner(),
    )
    assert result.incomplete is True
    assert result.incomplete_reason == "token_cap"


def test_ac3_two_tuple_probe_yields_count_zero_tokens(monkeypatch, corpus_tarball, corpus_hash, loaded_vault):
    """Wrappers that yield 2-tuples contribute zero to the token sum,
    so a 2-tuple-only stream cannot trip the token cap by itself."""
    monkeypatch.setattr(orch, "MAX_TOKENS_PER_SCAN", 1)

    def two_tuple_fn(_runner):
        # 2-tuples — no token telemetry. With cap=1, this should NOT trip.
        for i in range(3):
            yield f"P1.{i + 1}", {
                "raw": 1.0,
                "evidence_uri": f"https://evidence.local/tt-{i}",
                "evidence_sha256": "sha256:" + "c" * 64,
            }

    channel = WorkerChannel(max_queue=256)
    result = run_scan(
        _request(corpus_hash),
        loaded_vault,
        channel,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=two_tuple_fn,
        runner=_runner(),
    )
    assert result.incomplete_reason != "token_cap"


# ---------------------------------------------------------------------------
# AC#4 — wall-clock cap via mocked time.
# ---------------------------------------------------------------------------


def test_ac4_wallclock_cap_halts_with_mocked_time(monkeypatch, corpus_tarball, corpus_hash, loaded_vault):
    """Monkeypatch the orchestrator's `_now` so wall-clock elapses
    artificially per probe iteration. After enough iterations the
    accumulated elapsed exceeds the cap.
    """
    monkeypatch.setattr(orch, "MAX_WALL_CLOCK_SECONDS", 10)

    # The orchestrator calls _now() once before the loop (scan_started_at)
    # and once after each row. Synthesize a clock that returns 0.0 for the
    # start and increments by 5.0 per call thereafter, so the cap fires
    # after the third probe (elapsed=15 > 10).
    counter = count(start=0, step=5)

    def fake_now() -> float:
        return float(next(counter))

    monkeypatch.setattr(orch, "_now", fake_now)

    def slow_fn(_runner):
        for i in range(10):
            yield f"P1.{i + 1}", {
                "raw": 1.0,
                "evidence_uri": f"https://evidence.local/wc-{i}",
                "evidence_sha256": "sha256:" + "d" * 64,
            }

    channel = WorkerChannel(max_queue=256)
    result = run_scan(
        _request(corpus_hash),
        loaded_vault,
        channel,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=slow_fn,
        runner=_runner(),
    )
    assert result.incomplete is True
    assert result.incomplete_reason == "wallclock_cap"


# ---------------------------------------------------------------------------
# AC#5 — partial result reaches the Scorer.
# ---------------------------------------------------------------------------


def test_ac5_partial_rows_cross_ipc_before_cap(monkeypatch, corpus_tarball, corpus_hash, loaded_vault):
    """Rows emitted BEFORE the cap fire reach the Scorer side cleanly."""
    monkeypatch.setattr(orch, "MAX_SMOKE_DEV_PROBES_PER_SCAN", 4)

    def fn(_runner):
        for i in range(10):
            yield f"P1.{(i % 9) + 1}", {
                "raw": float(i),
                "evidence_uri": f"https://evidence.local/p-{i}",
                "evidence_sha256": "sha256:" + "e" * 64,
            }

    channel = WorkerChannel(max_queue=256)
    run_scan(
        _request(corpus_hash, scan_tier="smoke"),
        loaded_vault,
        channel,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=fn,
        runner=_runner(),
    )
    drained = _drain_channel(channel)
    assert len(drained) == 4
    # Every row passed the T4 serializer's allow-list (implicit by
    # construction — send_row would have raised otherwise).
    for row in drained:
        assert row["runner"] == "beaai-hosted-eval"


# ---------------------------------------------------------------------------
# 3-tuple shape — orchestrator rejects malformed token telemetry.
# ---------------------------------------------------------------------------


def test_orchestrator_rejects_negative_tokens(corpus_tarball, corpus_hash, loaded_vault):
    """Negative ``probe_tokens`` is a contract failure, not zero-fill."""
    def fn(_runner):
        yield (
            "P1.1",
            {
                "raw": 1.0,
                "evidence_uri": "https://evidence.local/neg",
                "evidence_sha256": "sha256:" + "f" * 64,
            },
            -5,  # negative
        )

    channel = WorkerChannel(max_queue=8)
    with pytest.raises(orch.OrchestratorError, match="non-negative"):
        run_scan(
            _request(corpus_hash),
            loaded_vault,
            channel,
            corpus_tarball_path=corpus_tarball,
            probe_run_fn=fn,
            runner=_runner(),
        )


def test_orchestrator_rejects_wrong_tuple_arity(corpus_tarball, corpus_hash, loaded_vault):
    def fn(_runner):
        yield ("P1.1",)  # 1-tuple

    channel = WorkerChannel(max_queue=8)
    with pytest.raises(orch.OrchestratorError):
        run_scan(
            _request(corpus_hash),
            loaded_vault,
            channel,
            corpus_tarball_path=corpus_tarball,
            probe_run_fn=fn,
            runner=_runner(),
        )


# ---------------------------------------------------------------------------
# BEAAA-7887 — bounded walker deadline preserves partial rows.
# ---------------------------------------------------------------------------


def test_probe_run_incomplete_flushes_partial_rows(
    corpus_tarball, corpus_hash, loaded_vault
):
    def fn(_runner):
        yield "P1.1", {
            "raw": 1.0,
            "evidence_uri": "https://evidence.local/deadline-row",
            "evidence_sha256": "sha256:" + "1" * 64,
        }
        raise ProbeRunIncompleteError(
            "customer_endpoint_unreachable_transport",
            "customer endpoint scan deadline exceeded",
        )

    channel = WorkerChannel(max_queue=8)
    result = run_scan(
        _request(corpus_hash),
        loaded_vault,
        channel,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=fn,
        runner=_runner(),
    )

    assert result.status == "incomplete"
    assert result.incomplete is True
    assert result.incomplete_reason == "customer_endpoint_unreachable_transport"
    assert result.rows_emitted == 1
    assert len(_drain_channel(channel)) == 1


def test_probe_run_incomplete_without_rows_returns_partial_result(
    corpus_tarball, corpus_hash, loaded_vault
):
    def fn(_runner):
        raise ProbeRunIncompleteError(
            "customer_endpoint_unreachable_transport",
            "customer endpoint scan deadline exceeded",
        )
        yield  # pragma: no cover

    channel = WorkerChannel(max_queue=8)
    result = run_scan(
        _request(corpus_hash),
        loaded_vault,
        channel,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=fn,
        runner=_runner(),
    )

    assert result.status == "incomplete"
    assert result.incomplete is True
    assert result.incomplete_reason == "customer_endpoint_unreachable_transport"
    assert result.rows_emitted == 0
    assert _drain_channel(channel) == []


# ---------------------------------------------------------------------------
# No cap breach → variance-band path still runs normally.
# ---------------------------------------------------------------------------


def test_no_cap_breach_yields_complete_status(corpus_tarball, corpus_hash, loaded_vault):
    """When no cap fires, T5's band check is the authority on incomplete."""
    def fn(_runner):
        for i in range(2):
            yield (
                f"P1.{i + 1}",
                {
                    "raw": 3.0,
                    "evidence_uri": f"https://evidence.local/ok-{i}",
                    "evidence_sha256": "sha256:" + "a" * 64,
                },
                10,
            )

    channel = WorkerChannel(max_queue=256)
    result = run_scan(
        _request(corpus_hash),
        loaded_vault,
        channel,
        corpus_tarball_path=corpus_tarball,
        probe_run_fn=fn,
        runner=_runner(),
    )
    assert result.incomplete is False
    assert result.incomplete_reason is None
    assert result.status == "complete"
