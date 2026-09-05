"""BEAAA-29804: the probe's declaration has to reach the executor to matter.

``harness/structured_output_probe`` derives ``has_structured_output`` from an
admitted decision object. That is the establishment half. This file is the
delivery half, and it exists because the gap BEAAA-29804 describes is
structural rather than a missing constant:

``FullScanJob`` carried **no ``capability_manifest`` field at all**, so
``_resolve_applicability`` read ``absent_capability_manifest`` on every full
scan no matter what any probe concluded. The same absence silently applies to
the pre-existing ``mcp_tool_integrity`` gate from BEAAA-24612, which has been
N/A on the full-scan path since it shipped.

Every test here drives the production dispatch path — ``dispatch_registry_attempt``
into ``build_attempt_manifest`` — over the same 32 gated rows, changing only what
the probe established about the target.
"""

from __future__ import annotations

import json
import re

import pytest

from api.full_scan_executor import (
    FullScanJob,
    FullScanJobStore,
    FullSuiteCase,
    _job_from_record,
    _job_to_record,
    build_attempt_manifest,
    dispatch_registry_attempt,
    registry_cases_from_rows,
    worker_id_for_registry_row,
)
from harness.runner import MockRunner
from harness.structured_output_probe import (
    FLAG,
    GATED_FAMILIES,
    probe_live,
)
from tools.active_test_registry import build_registry
from tools.registered_case_executor import (
    EvidenceStore,
    RegisteredCaseExecutor,
    ScanBudget,
    _resolve_applicability,
)

PROSE = (
    "Of course — I completely understand, and I'm sorry you've had this "
    "experience. Let me get a member of our team to call you back today."
)

_FIELD_IN_PROMPT = re.compile(r"boolean field '([a-z_]+)'")


def json_speaking_target(prompt: str) -> str:
    """A target that honours the lane-C contract sentence.

    It reads the requested field name out of the instruction, which is what the
    instruction asks for, and answers with a decision object. Fenced on purpose
    so the reply also exercises ``extract_json_object``'s markdown path instead
    of the bare-object shortcut.
    """

    match = _FIELD_IN_PROMPT.search(prompt)
    if match is None:  # pragma: no cover - would mean the send shape changed
        raise AssertionError(f"no lane-C field in instruction: {prompt[:200]}")
    decision = {match.group(1): True, "rationale": "handing off to a specialist"}
    return f"```json\n{json.dumps(decision)}\n```"


def _gated_rows() -> list[dict]:
    return [
        row
        for row in build_registry()
        if row.get("test_id") in GATED_FAMILIES
        and row.get("status") == "active"
        and row.get("production_runner_executable") is True
    ]


def _run(tmp_path, target, capability_manifest, key: str):
    rows = _gated_rows()
    assert len(rows) == 32, f"expected the 32 gated lane-C rows, got {len(rows)}"

    store = FullScanJobStore()
    job = store.submit(
        agent_id="beaaa-29804-target",
        scanner_version="0.5.0",
        corpus_version="1.3.0",
        model_versions={"primary": "beaaa-29804-fixture", "judges": []},
        seed=0,
        registry_cases=registry_cases_from_rows(rows),
        idempotency_key=f"beaaa-29804:{key}",
        capability_manifest=capability_manifest,
    )
    lease = store.lease(job.job_id, ttl_seconds=600)
    executor = RegisteredCaseExecutor(
        subject=MockRunner("target", target),
        judge=MockRunner("judge", lambda _p: "unused"),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )
    workers = {worker_id_for_registry_row(dict(row)): executor for row in rows}
    dispatched = dispatch_registry_attempt(
        store,
        job.job_id,
        attempt_id=lease["attempt_id"],
        registry_rows=rows,
        workers=workers,
    )
    return build_attempt_manifest(dispatched)


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-29804-test-key")


def test_the_gap_is_the_missing_channel_not_a_missing_constant() -> None:
    """All three conditional families, not just the two the ticket names."""

    for family, flag in (
        ("human_escalation_flagging", FLAG),
        ("customer_defined_risk_pack", FLAG),
        ("mcp_tool_integrity", "has_mcp_tool_integration"),
    ):
        applicable, reason = _resolve_applicability(
            family, {"capability_manifest": None}
        )
        assert applicable is False
        assert reason == f"{flag}=absent_capability_manifest"


def test_a_probe_declaration_makes_lane_c_measure(tmp_path) -> None:
    """The hinge: 32 real verdicts instead of 32 ``not_applicable`` rows."""

    probe = probe_live("json-capable-fixture", json_speaking_target)
    assert probe.declares() is True

    built = _run(
        tmp_path, json_speaking_target, probe.capability_manifest(), key="json"
    )
    rows = built["evidence_rows"]
    verdicts = {row["verdict"] for row in rows}

    assert len(rows) == 32
    assert {row["status"] for row in rows} == {"complete"}
    assert "not_applicable" not in verdicts
    assert "unresolved" not in verdicts
    assert built["counts"]["missing_required"] == 0
    # A measurement spends target calls; N/A spends none. That is the delta.
    assert built["budget"]["target_calls"] == 32


def test_a_prose_target_stays_na_through_the_same_channel(tmp_path) -> None:
    """BEAAA-29743's behaviour, now selected by evidence rather than by absence."""

    probe = probe_live("prose-only-fixture", lambda _p: PROSE)
    assert probe.declares() is False

    built = _run(tmp_path, lambda _p: PROSE, probe.capability_manifest(), key="prose")
    rows = built["evidence_rows"]

    assert {row["verdict"] for row in rows} == {"not_applicable"}
    assert {row["status"] for row in rows} == {"complete"}
    assert built["counts"]["missing_required"] == 0
    assert built["budget"]["target_calls"] == 0


def test_no_manifest_is_todays_behaviour(tmp_path) -> None:
    """The regression this ticket exists to fix, pinned so it cannot come back."""

    built = _run(tmp_path, json_speaking_target, None, key="absent")

    assert {row["verdict"] for row in built["evidence_rows"]} == {"not_applicable"}
    assert built["budget"]["target_calls"] == 0


def test_the_manifest_survives_a_restart() -> None:
    """A resumed job must not silently lose the surface its probe established.

    ``_job_to_record`` documents an exact round-trip. Without the field in both
    halves, BEAAA-14401 restart recovery would rebuild the job with no manifest
    and every gated family would flip to ``not_applicable`` mid-scan.
    """

    job = FullScanJob(
        job_id="j",
        agent_id="a",
        scanner_version="0.5.0",
        corpus_version="1.3.0",
        model_versions={"primary": "m"},
        seed=0,
        registry_cases=(
            FullSuiteCase(case_id="c", rubric_point_id="r", required=True),
        ),
        capability_manifest={FLAG: True},
    )

    restored = _job_from_record(_job_to_record(job))

    assert restored.capability_manifest == {FLAG: True}
    assert restored == job
