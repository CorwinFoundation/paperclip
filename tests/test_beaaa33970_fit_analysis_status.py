"""BEAAA-33970: the fit-to-bind guard's ``analysis_status`` term was unsatisfiable.

``_manifest_unfitness`` compares a manifest's ``analysis_status`` against
``FIT_ANALYSIS_STATUS``, which read ``"analysis_complete"``. No producer in this
tree emits that string. ``build_attempt_manifest`` writes ``"complete"`` when a
run carries no incomplete reasons, ``scripts/regrade_beaaa33199.py`` writes the
same, and ``demo-real-agent`` pins ``"complete"`` independently. So a manifest
that declared its analysis COMPLETE was refused for saying so, and only a
manifest that said ``analysis_incomplete`` could reach the guard's other terms.

The defect stayed invisible because every manifest committed here carries
``analysis_incomplete`` - refused for the right reason, with the dead term
riding along in the reason list. The BEAAA-33970 re-derivation is the first
thing to produce a healthy value, and it hit the term at once.

The first test below is the one that matters: it builds a manifest with the real
executor and asserts the guard accepts its ``analysis_status``. That couples the
constant to its producer, so neither can move without reddening this file. The
literal pin is kept separately so the coupling test cannot silently agree with a
producer that has itself drifted.
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
from harness.runner import MockRunner
from tools.active_test_registry import build_registry
from tools.beaaa31092_content_slice import FIT_ANALYSIS_STATUS, _manifest_unfitness
from tools.registered_case_executor import (
    EvidenceStore,
    RegisteredCaseExecutor,
    ScanBudget,
)

GATED = ("human_escalation_flagging", "customer_defined_risk_pack")
PROSE = (
    "Of course - I completely understand, and I'm sorry you've had this "
    "experience. Let me get a member of our team to call you back today."
)

ANALYSIS_STATUS_TERM = "analysis_status:"


@pytest.fixture()
def healthy_manifest(tmp_path, monkeypatch):
    """A manifest built by production code whose analysis actually completed.

    Same path as tests/test_lane_c_na_satisfies_required_beaaa29743.py: 32
    applicability-gated lane-C rows against a prose-only target reach a terminal
    N/A, so the run finishes with no incomplete reasons.
    """

    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-33970-test-key")
    rows = [
        row
        for row in build_registry()
        if row.get("test_id") in GATED
        and row.get("status") == "active"
        and row.get("production_runner_executable") is True
    ]
    assert len(rows) == 32, f"expected the 32 gated lane-C rows, got {len(rows)}"

    def judge(prompt: str) -> str:  # pragma: no cover - reaching this is the bug
        raise AssertionError("an N/A case must not reach the judge")

    executor = RegisteredCaseExecutor(
        subject=MockRunner("target", lambda _prompt: PROSE),
        judge=MockRunner("judge", judge),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )
    store = FullScanJobStore()
    job = store.submit(
        agent_id="prose-only-target",
        scanner_version="0.5.0",
        corpus_version="1.3.0",
        model_versions={"primary": "prose-fixture-v0", "judges": []},
        seed=0,
        registry_cases=registry_cases_from_rows(rows),
        idempotency_key="beaaa-33970:prose-only:full",
    )
    lease = store.lease(job.job_id, ttl_seconds=600)
    dispatched = dispatch_registry_attempt(
        store,
        job.job_id,
        attempt_id=lease["attempt_id"],
        registry_rows=rows,
        workers={worker_id_for_registry_row(dict(row)): executor for row in rows},
    )
    return build_attempt_manifest(dispatched)


def test_a_manifest_the_executor_calls_complete_is_fit_on_analysis_status(
    healthy_manifest,
) -> None:
    """The hinge. Before the fix this failed: the guard refused `complete`."""

    assert not healthy_manifest["incomplete_reasons"]
    produced = healthy_manifest["analysis_status"]
    assert produced == FIT_ANALYSIS_STATUS, (
        f"the executor emits analysis_status={produced!r} for a run with no "
        f"incomplete reasons, but the guard treats {FIT_ANALYSIS_STATUS!r} as "
        "the only fit value; the term is unsatisfiable while these disagree"
    )
    reasons = _manifest_unfitness(healthy_manifest)
    assert not [r for r in reasons if r.startswith(ANALYSIS_STATUS_TERM)], reasons


def test_an_incomplete_analysis_is_still_refused(healthy_manifest) -> None:
    """Non-vacuity: the fix must not turn the term off, only point it correctly."""

    refused = dict(healthy_manifest, analysis_status="analysis_incomplete")
    assert f"{ANALYSIS_STATUS_TERM}analysis_incomplete" in _manifest_unfitness(refused)

    absent = {key: value for key, value in healthy_manifest.items() if key != "analysis_status"}
    assert not [
        r for r in _manifest_unfitness(absent) if r.startswith(ANALYSIS_STATUS_TERM)
    ], "absence is not a self-disownment; a silent manifest keeps its exemption"


def test_the_fit_value_is_pinned_to_a_literal() -> None:
    """Pinned separately so the coupling test cannot agree with a drifted producer."""

    assert FIT_ANALYSIS_STATUS == "complete"
