"""BEAAA-29743: an N/A lane-C row must satisfy its required case, not go missing.

The applicability gate stops the 32 human_escalation_flagging and
customer_defined_risk_pack rows from failing against a prose-only target. That
is only useful to BEAAA-29082 if the terminal N/A outcome reaches
``build_attempt_manifest`` as a ``status == "complete"`` row carrying an
``evidence_ref``: ``_satisfied_case_ids`` counts nothing else, and a required
case absent from that set lands in ``missing_required`` exactly as the original
LaneCError did.

Reading the code says it does. This runs it. The whole path is production code —
``dispatch_registry_attempt`` builds the evidence rows and
``build_attempt_manifest`` computes the counts; the only fixtures are the target
(which speaks prose, like the VAPI assistant) and the judge (which must never be
reached).
"""

from __future__ import annotations

import pytest

from api.full_scan_executor import (
    FullScanJobStore,
    build_attempt_manifest,
    dispatch_registry_attempt,
    registry_cases_from_rows,
    worker_id_for_registry_row,
)
from tools.active_test_registry import build_registry
from tools.registered_case_executor import (
    EvidenceStore,
    RegisteredCaseExecutor,
    ScanBudget,
)
from harness.runner import MockRunner

GATED = ("human_escalation_flagging", "customer_defined_risk_pack")
PROSE = (
    "Of course — I completely understand, and I'm sorry you've had this "
    "experience. Let me get a member of our team to call you back today."
)


def _gated_rows() -> list[dict]:
    return [
        row
        for row in build_registry()
        if row.get("test_id") in GATED
        and row.get("status") == "active"
        and row.get("production_runner_executable") is True
    ]


def _prose_executor(tmp_path) -> RegisteredCaseExecutor:
    def judge(prompt: str) -> str:  # pragma: no cover - reaching this is the bug
        raise AssertionError("an N/A case must not reach the judge")

    return RegisteredCaseExecutor(
        subject=MockRunner("target", lambda _prompt: PROSE),
        judge=MockRunner("judge", judge),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )


@pytest.fixture()
def manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-29743-test-key")
    rows = _gated_rows()
    assert len(rows) == 32, f"expected the 32 gated lane-C rows, got {len(rows)}"

    store = FullScanJobStore()
    job = store.submit(
        agent_id="prose-only-target",
        scanner_version="0.5.0",
        corpus_version="1.3.0",
        model_versions={"primary": "prose-fixture-v0", "judges": []},
        seed=0,
        registry_cases=registry_cases_from_rows(rows),
        idempotency_key="beaaa-29743:prose-only:full",
    )
    lease = store.lease(job.job_id, ttl_seconds=600)
    executor = _prose_executor(tmp_path)
    workers = {worker_id_for_registry_row(dict(row)): executor for row in rows}

    dispatched = dispatch_registry_attempt(
        store,
        job.job_id,
        attempt_id=lease["attempt_id"],
        registry_rows=rows,
        workers=workers,
    )
    return build_attempt_manifest(dispatched), dispatched


def test_the_32_na_rows_do_not_count_as_missing_required(manifest) -> None:
    """The hinge. If this fails, BEAAA-29082 still reports missing_required > 0."""

    built, job = manifest

    assert len(job.registry_cases) == 32
    assert built["counts"]["missing_required"] == 0


def test_every_na_row_is_complete_and_carries_an_evidence_ref(manifest) -> None:
    """What _satisfied_case_ids actually keys on, asserted per row."""

    built, _job = manifest
    rows = built["evidence_rows"]

    assert len(rows) == 32
    assert {row["status"] for row in rows} == {"complete"}
    assert all(row["evidence_ref"] for row in rows)
    assert {row["verdict"] for row in rows} == {"not_applicable"}


def test_the_target_and_judge_were_never_called(manifest) -> None:
    """N/A is a declaration of scope, not a measurement. It may not spend calls."""

    built, _job = manifest

    assert built["budget"]["target_calls"] == 0
    assert built["budget"]["judge_calls"] == 0


def test_removing_the_gate_puts_all_32_back_in_missing_required(
    tmp_path, monkeypatch
) -> None:
    """Gate-removal mutant (BEAAA-24008 ruling E).

    Neutralise the applicability lookup and the live BEAAA-29743 defect returns:
    the prose answer raises LaneCError, the row checkpoints as ``failed``, and
    all 32 required cases land back in ``missing_required``. Without this, the
    assertions above could pass for reasons unrelated to the gate.
    """

    import tools.registered_case_executor as rce

    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-29743-test-key")
    monkeypatch.setattr(rce, "_declared_applicability_flag", lambda _family: None)

    rows = _gated_rows()
    store = FullScanJobStore()
    job = store.submit(
        agent_id="prose-only-target",
        scanner_version="0.5.0",
        corpus_version="1.3.0",
        model_versions={"primary": "prose-fixture-v0", "judges": []},
        seed=0,
        registry_cases=registry_cases_from_rows(rows),
        idempotency_key="beaaa-29743:prose-only:mutant",
    )
    lease = store.lease(job.job_id, ttl_seconds=600)
    executor = _prose_executor(tmp_path)
    workers = {worker_id_for_registry_row(dict(row)): executor for row in rows}

    dispatched = dispatch_registry_attempt(
        store,
        job.job_id,
        attempt_id=lease["attempt_id"],
        registry_rows=rows,
        workers=workers,
    )
    built = build_attempt_manifest(dispatched)

    assert built["counts"]["missing_required"] == 32
    assert {row["status"] for row in built["evidence_rows"]} == {"failed"}
    assert {row["verdict"] for row in built["evidence_rows"]} == {"unresolved"}
    assert built["budget"]["target_calls"] == 32
