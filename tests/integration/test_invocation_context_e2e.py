"""BEAAA-5486 — production scan runtime invocation-context wiring.

HTTP-level regression for `/scanner/v1/rescans` to ensure an explicitly
supplied `scanner_invocation_context` reaches the production ScoreRuntime
score-inputs as `invocation_context`, including explicit
`applicability_decisions`.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

try:
    from fastapi.testclient import TestClient
except ImportError as exc:  # pragma: no cover
    raise unittest.SkipTest(f"fastapi not installed: {exc}")

from api.app import create_app
from api.bundle import BundleStore
from api.scan_runtime_factory import (
    build_v0_corpus_tarball,
    make_production_scan_runtime,
    v0_corpus_snapshot_hash,
)
from scanners.live_probe_orchestrator import (
    DimResult,
    LIVE_PROBE_VARIANCE_BAND,
    ScanResult,
)
import api.scan_runtime_factory as scan_runtime_factory


TENANT_ID = "tenant-5486"
AGENT_ID = "agent-5486"


def _scan_result(scan_id: str) -> ScanResult:
    return ScanResult(
        scan_id=scan_id,
        status="complete",
        scanner_version="0.5.0",
        corpus_version="0.1.0",
        rows_emitted=4,
        k_replicates=5,
        live_probe_variance_band=LIVE_PROBE_VARIANCE_BAND,
        dim_results=(
            DimResult(dim_id="P1.1", raw=3.0, spread=0.0, replicates=(3.0,) * 5),
            DimResult(dim_id="P2.1", raw=2.5, spread=0.0, replicates=(2.5,) * 5),
            DimResult(dim_id="P3.1", raw=2.0, spread=0.0, replicates=(2.0,) * 5),
            DimResult(dim_id="P4.1", raw=3.5, spread=0.0, replicates=(3.5,) * 5),
        ),
        spread_per_dim={"P1.1": 0.0, "P2.1": 0.0, "P3.1": 0.0, "P4.1": 0.0},
        pillar_spread={"P1": 0.0, "P2": 0.0, "P3": 0.0, "P4": 0.0},
        total_spread=0.0,
        incomplete=False,
        incomplete_reason=None,
    )


class _DummyScorerProcess:
    def is_alive(self) -> bool:
        return False

    def terminate(self) -> None:  # pragma: no cover
        return None

    def join(self, timeout: float | None = None) -> None:  # pragma: no cover
        return None

    def close(self) -> None:  # pragma: no cover
        return None


def _fake_prober_target(
    *,  # noqa: ARG001 — kwargs-only in prod path; tests keep explicit surface
    scan_request: Any,
    vault: Any,
    channel: Any,
    corpus_tarball_path: str,
    probe_run_fn: Any,
    runner_factory: Any,
    agent_endpoint_url: str,
    result_queue: Any,
) -> None:
    # Lightweight prober path for test process model parity: no network,
    # no vault usage, no score input mutation.
    del vault, channel, corpus_tarball_path, probe_run_fn, runner_factory
    del agent_endpoint_url
    result_queue.put(("ok", _scan_result(scan_request.scan_id)))


def _body(corpus_hash: str, *, applicability_decisions: dict[str, Any]) -> dict[str, Any]:
    return {
        "tenant_id": TENANT_ID,
        "agent_id": AGENT_ID,
        "agent_endpoint": {"host": "customer.example"},
        "credential_ref": "phase1-probe.v1",
        "credential_secret": "secret-5486",
        "corpus_snapshot_hash": corpus_hash,
        "scanner_version": "0.5.0",
        "corpus_version": "0.1.0",
        "seed": 42,
        "scan_mode": "live_probe",
        "k_replicates": 5,
        "requested_pillars": ["GOVERNANCE", "ROBUSTNESS", "BIAS_FAIRNESS", "OPERATIONS"],
        "scanner_invocation_context": {
            "purpose": "mid_term",
            "applicability_decisions": applicability_decisions,
        },
    }


def _runtime_with_instrumented_scorer(tmp_path: Path) -> Any:
    corpus_tarball_path = build_v0_corpus_tarball(tmp_path / "corpus")
    corpus_hash = v0_corpus_snapshot_hash(corpus_tarball_path)
    runtime = make_production_scan_runtime(
        corpus_tarball_path=corpus_tarball_path,
        runner_factory=lambda *_: None,
        probe_run_fn=lambda _runner: iter(()),
        score_runtime=object(),
    )
    return runtime, corpus_hash


def test_rescans_includes_invocation_context_in_score_inputs(
    tmp_path: Path, monkeypatch: Any
) -> None:
    captured: dict[str, Any] = {}
    runtime, corpus_hash = _runtime_with_instrumented_scorer(tmp_path)
    runtime._agent_ids_with_prior_scan.add(AGENT_ID)

    def fake_spawn_scorer(
        *,  # noqa: ARG001
        channel: Any,
        on_validated_row: Any,
        score_runtime: Any,
        score_inputs: dict[str, Any] | None,
        row_recv_timeout_seconds: float | None = None,
    ) -> _DummyScorerProcess:
        del channel, on_validated_row, score_runtime, row_recv_timeout_seconds
        captured["score_inputs"] = score_inputs
        return _DummyScorerProcess()

    monkeypatch.setattr(scan_runtime_factory, "spawn_scorer", fake_spawn_scorer)
    monkeypatch.setattr(scan_runtime_factory, "_prober_target", _fake_prober_target)

    client = TestClient(
        create_app(scan_runtime=runtime, bundles=BundleStore())
    )
    applicability_decisions = {
        "P1.1": {"fires": True},
        "P2.1": {
            "fires": False,
            "na_justification": "Customer out-of-scope dimension.",
            "na_justification_key": "CUSTOMER_NA_V03",
        },
    }
    response = client.post(
        "/scanner/v1/rescans",
        headers={"X-Tenant-Id": TENANT_ID},
        json=_body(corpus_hash, applicability_decisions=applicability_decisions),
    )
    assert response.status_code == 202, response.text

    assert captured["score_inputs"] is not None
    score_inputs = captured["score_inputs"]
    invocation_context = score_inputs.get("invocation_context")
    assert isinstance(invocation_context, dict), f"score_inputs={score_inputs!r}"
    assert invocation_context["purpose"] == "mid_term"
    assert (
        invocation_context["applicability_decisions"]
        == applicability_decisions
    )
