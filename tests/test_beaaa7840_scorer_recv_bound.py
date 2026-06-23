"""BEAAA-7840 — bound scorer return-channel receive in production runtime."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

import api.scan_runtime_factory as scan_runtime_factory
from api.scan_runtime_factory import (
    build_v0_corpus_tarball,
    make_production_scan_runtime,
    v0_corpus_snapshot_hash,
)
from scanners.live_probe_orchestrator import (
    LIVE_PROBE_VARIANCE_BAND,
    DimResult,
    OrchestratorError,
    ScanRequest,
    ScanResult,
)


class _ExitedScorerProcess:
    exitcode = 1

    def is_alive(self) -> bool:
        return False

    def terminate(self) -> None:  # pragma: no cover
        return None

    def join(self, timeout: float | None = None) -> None:  # pragma: no cover
        return None

    def close(self) -> None:  # pragma: no cover
        return None


def _scan_result(scan_id: str) -> ScanResult:
    return ScanResult(
        scan_id=scan_id,
        status="complete",
        scanner_version="0.4.2",
        corpus_version="0.1.0",
        rows_emitted=1,
        k_replicates=5,
        live_probe_variance_band=LIVE_PROBE_VARIANCE_BAND,
        dim_results=(
            DimResult(dim_id="P1.1", raw=3.0, spread=0.0, replicates=(3.0,) * 5),
        ),
        spread_per_dim={"P1.1": 0.0},
        pillar_spread={"P1": 0.0},
        total_spread=0.0,
        incomplete=False,
        incomplete_reason=None,
    )


def _fake_prober_target(
    *,  # noqa: ARG001 — mirrors production kwargs surface
    scan_request: ScanRequest,
    vault: Any,
    channel: Any,
    corpus_tarball_path: str,
    probe_run_fn: Any,
    runner_factory: Any,
    agent_endpoint_host: str,
    result_queue: Any,
) -> None:
    del vault, channel, corpus_tarball_path, probe_run_fn, runner_factory
    del agent_endpoint_host
    result_queue.put(("ok", _scan_result(scan_request.scan_id)))


def _scan_request(corpus_hash: str) -> ScanRequest:
    return ScanRequest(
        scan_id="scan-beaaa7840",
        tenant_id="tenant-beaaa7840",
        agent_id="agent-beaaa7840",
        agent_endpoint="https://customer.example/scan",
        credential_ref="cred-beaaa7840",
        corpus_snapshot_hash=corpus_hash,
        scanner_version="0.4.2",
        corpus_version="0.1.0",
        seed=42,
        scan_mode="live_probe",
        k_replicates=5,
        invocation_context={"purpose": "pre_bind"},
    )


def test_dead_scorer_envelope_recv_uses_short_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus_tarball_path = build_v0_corpus_tarball(tmp_path / "corpus")
    corpus_hash = v0_corpus_snapshot_hash(corpus_tarball_path)
    runtime = make_production_scan_runtime(
        corpus_tarball_path=corpus_tarball_path,
        runner_factory=lambda *_: None,
        probe_run_fn=lambda _runner: iter(()),
        score_runtime=object(),
        score_envelope_registry={},
    )

    def fake_spawn_scorer(**_: Any) -> _ExitedScorerProcess:
        return _ExitedScorerProcess()

    monkeypatch.setattr(scan_runtime_factory, "spawn_scorer", fake_spawn_scorer)
    monkeypatch.setattr(scan_runtime_factory, "_prober_target", _fake_prober_target)
    monkeypatch.setattr(
        scan_runtime_factory,
        "SCORER_ENVELOPE_DRAIN_TIMEOUT_SECONDS",
        0.1,
    )

    started = time.monotonic()
    with pytest.raises(OrchestratorError, match="scorer envelope queue drain failed"):
        runtime._run_scan_fn(_scan_request(corpus_hash), "secret-beaaa7840")
    elapsed = time.monotonic() - started

    assert elapsed < 1.5
