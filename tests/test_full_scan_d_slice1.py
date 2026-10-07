"""BEAAA-38957 Full Scan D Slice 1 — unit tests.

Covers:
1. Versioned paid pack id — resolve_pack, unknown pack rejection.
2. max_cost_usd hard cap — stops at cap, marks cost_cap_reached rows.
3. Per-target concurrency — second job for same (tenant, target) rejected.
4. Entitlement required + idempotent grant — can't submit without entitlement;
   idempotent grant does not double-consume.
5. Restart-resume mid-pack — no case run twice after simulated worker death.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from pathlib import Path

import pytest

from api.full_scan_executor import (
    EvidenceAttemptRow,
    FullScanExecutorError,
    FullScanJobStore,
    WorkerAttemptResult,
    dispatch_registry_attempt,
    registry_cases_from_rows,
)
from api.full_scan_job_persistence import FullScanJobPersistence
from api.full_scan_pack_registry import known_pack_ids, resolve_pack
from api.full_scan_entitlement import FullScanEntitlementStore
from api.full_scan_worker import FullScanQueueWorker
from api.full_suite_manifest import FullSuiteCase


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int = 1) -> None:
        self.now += timedelta(seconds=seconds)


def _make_registry_row(
    case_id: str,
    *,
    rubric_point_id: str = "P1.1",
    status: str = "active",
    executable: bool = True,
    judge_cost: float = 0.0,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "rubric_point_id": rubric_point_id,
        "risk_or_rubric_id": rubric_point_id,
        "status": status,
        "production_runner_executable": executable,
        "estimated_judge_cost_usd": judge_cost,
        "test_id": case_id,
        "version_pin": "v0.1",
    }


def _noop_worker(
    row: Mapping[str, Any], job: Any
) -> WorkerAttemptResult:
    return WorkerAttemptResult(
        status="complete",
        evidence_ref=f"evidence://{job.job_id}/{row.get('case_id')}.json",
        verdict="pass",
        estimated_judge_cost_usd=float(row.get("estimated_judge_cost_usd") or 0.0),
    )


def _costly_worker(cost_per_case: float):
    def _worker(row: Mapping[str, Any], job: Any) -> WorkerAttemptResult:
        return WorkerAttemptResult(
            status="complete",
            evidence_ref=f"evidence://{job.job_id}/{row.get('case_id')}.json",
            verdict="pass",
            estimated_judge_cost_usd=cost_per_case,
        )
    return _worker


def _make_store(
    *,
    clock: _Clock | None = None,
    persistent: bool = False,
) -> FullScanJobStore:
    pers = FullScanJobPersistence(":memory:") if persistent else None
    return FullScanJobStore(clock=clock or _Clock(), persistence=pers)


def _make_cases(*case_ids: str) -> tuple[FullSuiteCase, ...]:
    return tuple(FullSuiteCase(cid, "P1.1") for cid in case_ids)


def _make_registry_rows(*case_ids: str, judge_cost: float = 0.0) -> list[dict[str, Any]]:
    return [_make_registry_row(cid, judge_cost=judge_cost) for cid in case_ids]


# ---------------------------------------------------------------------------
# 1. Versioned paid pack registry
# ---------------------------------------------------------------------------


def test_pack_registry_known_packs():
    packs = known_pack_ids()
    assert "full-scan-conv-en@0.2" in packs
    assert "full-scan-conv-en@0.3" in packs


def test_pack_registry_resolve_v02():
    cases = resolve_pack("full-scan-conv-en@0.2")
    assert len(cases) == 108


def test_pack_registry_resolve_v03():
    cases = resolve_pack("full-scan-conv-en@0.3")
    assert len(cases) == 400


def test_pack_registry_unknown_raises():
    with pytest.raises(KeyError, match="full-scan-conv-en@99"):
        resolve_pack("full-scan-conv-en@99")


def test_submit_unknown_pack_raises():
    store = _make_store()
    with pytest.raises(FullScanExecutorError, match="Unknown pack_id"):
        store.submit(
            agent_id="agent-1",
            scanner_version="v0",
            corpus_version="1.0",
            model_versions={},
            seed=0,
            registry_cases=_make_cases("C1"),
            pack_id="no-such-pack@1.0",
        )


# ---------------------------------------------------------------------------
# 2. max_cost_usd hard cap
# ---------------------------------------------------------------------------


def test_cost_cap_stops_at_cap(tmp_path):
    """After the first case costs $0.10, remaining 2 cases are cost_cap_reached."""
    clock = _Clock()
    persistence = FullScanJobPersistence(str(tmp_path / "jobs.db"))
    store = FullScanJobStore(clock=clock, persistence=persistence)

    rows = _make_registry_rows("C1", "C2", "C3", judge_cost=0.10)
    cases = tuple(FullSuiteCase(r["case_id"], r["rubric_point_id"]) for r in rows)

    job = store.submit(
        agent_id="agent-1",
        scanner_version="v0",
        corpus_version="1.0",
        model_versions={},
        seed=0,
        registry_cases=cases,
        max_cost_usd=0.15,  # cap: one case passes, two are stopped
    )

    lease = store.lease(job.job_id, ttl_seconds=600)
    attempt_id = lease["attempt_id"]

    workers = {"promptfoo": _costly_worker(0.10)}

    # dispatch all three registry rows in one call
    final_job = dispatch_registry_attempt(
        store,
        job.job_id,
        attempt_id=attempt_id,
        registry_rows=rows,
        workers=workers,
        checkpoint_every=1,
    )

    reasons = {
        getattr(r, "incomplete_reason", None): 0 for r in final_job.evidence_rows
    }
    for r in final_job.evidence_rows:
        reasons[getattr(r, "incomplete_reason", None)] = (
            reasons.get(getattr(r, "incomplete_reason", None), 0) + 1
        )
    assert reasons.get("cost_cap_reached", 0) >= 1, (
        f"Expected cost_cap_reached rows; got {[r.incomplete_reason for r in final_job.evidence_rows]}"
    )


def test_cost_cap_na_is_not_pass(tmp_path):
    """N/A rows are NEVER a pass — incomplete_reason cost_cap_reached != pass."""
    clock = _Clock()
    persistence = FullScanJobPersistence(str(tmp_path / "jobs.db"))
    store = FullScanJobStore(clock=clock, persistence=persistence)

    rows = _make_registry_rows("C1", "C2", judge_cost=0.10)
    cases = tuple(FullSuiteCase(r["case_id"], r["rubric_point_id"]) for r in rows)

    job = store.submit(
        agent_id="agent-1",
        scanner_version="v0",
        corpus_version="1.0",
        model_versions={},
        seed=0,
        registry_cases=cases,
        max_cost_usd=0.05,  # cap below any case
    )

    lease = store.lease(job.job_id, ttl_seconds=600)
    attempt_id = lease["attempt_id"]

    final_job = dispatch_registry_attempt(
        store,
        job.job_id,
        attempt_id=attempt_id,
        registry_rows=rows,
        workers={"promptfoo": _costly_worker(0.10)},
        checkpoint_every=1,
    )

    for row in final_job.evidence_rows:
        if getattr(row, "incomplete_reason", None) == "cost_cap_reached":
            # Must NOT be treated as a pass
            assert getattr(row, "verdict", None) != "pass", (
                f"cost_cap_reached row has verdict=pass: {row}"
            )
            assert getattr(row, "status", None) == "incomplete"


# ---------------------------------------------------------------------------
# 3. Per-target concurrency
# ---------------------------------------------------------------------------


def test_per_target_concurrency_default_1():
    """Second job for same (tenant, target) raises when first is still running."""
    store = _make_store()
    cases = _make_cases("C1")

    # First job accepted
    store.submit(
        agent_id="agent-1",
        scanner_version="v0",
        corpus_version="1.0",
        model_versions={},
        seed=0,
        registry_cases=cases,
        tenant_id="tenant-A",
        target_id="target-X",
        max_concurrent_per_target=1,
    )

    # Second job for same tenant+target should be rejected
    with pytest.raises(FullScanExecutorError, match="Per-target concurrency limit"):
        store.submit(
            agent_id="agent-2",
            scanner_version="v0",
            corpus_version="1.0",
            model_versions={},
            seed=1,
            registry_cases=cases,
            tenant_id="tenant-A",
            target_id="target-X",
            max_concurrent_per_target=1,
        )


def test_per_target_concurrency_different_target_allowed():
    """Jobs for different targets on same tenant are allowed."""
    store = _make_store()
    cases = _make_cases("C1")

    store.submit(
        agent_id="agent-1",
        scanner_version="v0",
        corpus_version="1.0",
        model_versions={},
        seed=0,
        registry_cases=cases,
        tenant_id="tenant-A",
        target_id="target-X",
        max_concurrent_per_target=1,
    )

    # Different target — should succeed
    store.submit(
        agent_id="agent-2",
        scanner_version="v0",
        corpus_version="1.0",
        model_versions={},
        seed=1,
        registry_cases=cases,
        tenant_id="tenant-A",
        target_id="target-Y",
        max_concurrent_per_target=1,
    )


def test_per_target_concurrency_limit_2():
    """With limit=2, third job is rejected, second is accepted."""
    store = _make_store()
    cases = _make_cases("C1")
    kwargs = dict(
        agent_id="a",
        scanner_version="v0",
        corpus_version="1.0",
        model_versions={},
        seed=0,
        registry_cases=cases,
        tenant_id="T",
        target_id="X",
        max_concurrent_per_target=2,
    )
    store.submit(**kwargs)
    store.submit(**kwargs)  # second — allowed
    with pytest.raises(FullScanExecutorError, match="Per-target concurrency limit"):
        store.submit(**kwargs)  # third — rejected


# ---------------------------------------------------------------------------
# 4. Entitlement required + idempotent grant
# ---------------------------------------------------------------------------


def test_entitlement_required_for_paid_pack():
    """Submitting a paid-pack job without an entitlement raises."""
    ent_store = FullScanEntitlementStore(":memory:")
    store = _make_store()

    with pytest.raises(FullScanExecutorError, match="No unconsumed entitlement"):
        store.submit(
            agent_id="agent-1",
            scanner_version="v0",
            corpus_version="1.0",
            model_versions={},
            seed=0,
            registry_cases=_make_cases("C1"),
            pack_id="full-scan-conv-en@0.2",
            tenant_id="tenant-A",
            entitlement_store=ent_store,
        )


def test_entitlement_consumed_on_job_accept():
    """Accepting a paid-pack job consumes the entitlement."""
    ent_store = FullScanEntitlementStore(":memory:")
    ent_store.grant(tenant="T", pack_id="full-scan-conv-en@0.2", max_cost_usd=5.0)

    assert ent_store.check(tenant="T", pack_id="full-scan-conv-en@0.2")

    store = _make_store()
    store.submit(
        agent_id="agent-1",
        scanner_version="v0",
        corpus_version="1.0",
        model_versions={},
        seed=0,
        registry_cases=_make_cases("C1"),
        pack_id="full-scan-conv-en@0.2",
        tenant_id="T",
        entitlement_store=ent_store,
    )

    # After acceptance, entitlement is consumed
    assert not ent_store.check(tenant="T", pack_id="full-scan-conv-en@0.2")


def test_entitlement_grant_idempotent():
    """Granting with the same external_ref is idempotent (returns same record)."""
    ent_store = FullScanEntitlementStore(":memory:")
    r1 = ent_store.grant(
        tenant="T", pack_id="p@1", max_cost_usd=1.0, external_ref="ref-001"
    )
    r2 = ent_store.grant(
        tenant="T", pack_id="p@1", max_cost_usd=1.0, external_ref="ref-001"
    )
    assert r1["entitlement_id"] == r2["entitlement_id"]
    assert r1["consumed"] == 0


def test_entitlement_idempotent_accept(tmp_path):
    """Submitting same idempotency_key twice returns the same job."""
    ent_store = FullScanEntitlementStore(":memory:")
    ent_store.grant(tenant="T", pack_id="full-scan-conv-en@0.2", max_cost_usd=5.0)
    ent_store.grant(tenant="T", pack_id="full-scan-conv-en@0.2", max_cost_usd=5.0)

    store = _make_store()
    kwargs = dict(
        agent_id="agent-1",
        scanner_version="v0",
        corpus_version="1.0",
        model_versions={},
        seed=0,
        registry_cases=_make_cases("C1"),
        pack_id="full-scan-conv-en@0.2",
        tenant_id="T",
        entitlement_store=ent_store,
        idempotency_key="idem-001",
    )
    j1 = store.submit(**kwargs)
    j2 = store.submit(**kwargs)
    assert j1.job_id == j2.job_id


# ---------------------------------------------------------------------------
# 5. Restart-resume mid-pack — no case run twice
# ---------------------------------------------------------------------------


def test_restart_resume_no_case_run_twice(tmp_path):
    """Kill worker after k cases; resume; total distinct run cases == total cases."""
    db_path = str(tmp_path / "jobs.db")
    clock = _Clock()

    # Registry: 4 cases
    all_rows = _make_registry_rows("C1", "C2", "C3", "C4")
    cases = tuple(FullSuiteCase(r["case_id"], r["rubric_point_id"]) for r in all_rows)

    run_log: list[str] = []

    def _logging_worker(row: Mapping[str, Any], job: Any) -> WorkerAttemptResult:
        run_log.append(str(row.get("case_id")))
        return WorkerAttemptResult(
            status="complete",
            evidence_ref=f"evidence://{job.job_id}/{row.get('case_id')}.json",
            verdict="pass",
        )

    workers = {"promptfoo": _logging_worker}

    # First worker run — processes 2 cases then is "killed" (max_cases=2).
    persistence1 = FullScanJobPersistence(db_path)
    store1 = FullScanJobStore(clock=clock, persistence=persistence1)
    job = store1.submit(
        agent_id="a",
        scanner_version="v0",
        corpus_version="1.0",
        model_versions={},
        seed=0,
        registry_cases=cases,
    )
    worker1 = FullScanQueueWorker(
        store1,
        registry_rows=all_rows,
        workers=workers,
        lease_ttl_seconds=600,
    )
    result1 = worker1.run_once(max_cases=2)
    assert result1.status == "interrupted"
    assert result1.processed_cases == 2

    # Simulate process restart: new store loads from same DB.
    clock.advance(700)  # advance past lease TTL
    persistence2 = FullScanJobPersistence(db_path)
    store2 = FullScanJobStore(clock=clock, persistence=persistence2)
    store2.recover()  # clears stale lease

    worker2 = FullScanQueueWorker(
        store2,
        registry_rows=all_rows,
        workers=workers,
        lease_ttl_seconds=600,
    )
    result2 = worker2.run_once()
    assert result2.status == "finalized"

    # Verify no case was run twice
    assert len(run_log) == len(set(run_log)), (
        f"Duplicate case runs detected: {run_log}"
    )
    assert set(run_log) == {"C1", "C2", "C3", "C4"}
