"""Pin the upstream half of the provider-rejection link.

BEAAA-31906 established that an OpenRouter HTTP 403 credit cap was being
reported as malformed target output (``format``) across 2,234 rows of the
2026-09-10 nightly zoo run.  The fix has two halves:

* upstream -- ``_incomplete_reason_for_exception`` turns the runner exception
  into ``upstream_http_<status>`` instead of ``case_execution_error:<type>``;
* downstream -- ``_incomplete_group`` buckets ``upstream_http_*`` as
  ``upstream_http``.

Only the downstream half had tests.  Both existing suites
(``test_beaaa31590_zoo_incomplete_reason``, ``test_beaaa31622_garak_incomplete_group``)
start from a row that already carries the reason code, so a refactor that
dropped the status extraction would leave every one of them green while every
403 silently went back to reporting as ``format``.  These tests cover the
extraction itself and join the two halves end to end.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import scripts.scheduled_scan as scheduled_scan
from harness.runner import MockRunner, RunnerError
from tools.registered_case_executor import (
    EvidenceStore,
    RegisteredCaseExecutor,
    ScanBudget,
    _incomplete_reason_for_exception,
)


# Body taken verbatim from a retained 2026-09-10 zoo evidence row.
OPENROUTER_403 = (
    'openrouter HTTP 403: {"error":{"message":"Key limit exceeded '
    '(total limit).","code":403}}'
)


def _row() -> dict[str, object]:
    return {
        "test_id": "p1_1_factuality_golden",
        "case_id": "P1.1-FAC-001",
        "status": "active",
        "pillar": "OPS",
        "category": "factuality",
        "risk_or_rubric_id": "P1.1",
        "source_pack": "rubric/probe_mappings/p1_1_factuality_golden.yaml",
        "runner": "bench_wrapper",
        "version_pin": "p1_1_factuality_golden@0.1.0",
        "production_runner_executable": True,
    }


def test_real_openrouter_403_body_yields_upstream_reason_code() -> None:
    assert (
        _incomplete_reason_for_exception(RunnerError(OPENROUTER_403))
        == "upstream_http_403"
    )


@pytest.mark.parametrize("status", ["401", "429", "500", "503"])
def test_status_is_extracted_not_hardcoded_to_403(status: str) -> None:
    """The 403 was only the status we happened to hit first."""

    exc = RunnerError(f"openrouter HTTP {status}: upstream said no")
    assert _incomplete_reason_for_exception(exc) == f"upstream_http_{status}"


def test_lowercase_status_line_still_extracts() -> None:
    """The IGNORECASE flag is load-bearing; adapters do not agree on casing."""

    assert (
        _incomplete_reason_for_exception(RunnerError("anthropic http 429: rate limited"))
        == "upstream_http_429"
    )


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("target returned an empty completion"),
        RunnerError("connection reset by peer"),
        RuntimeError("HTTP transport failure with no status"),
    ],
)
def test_exception_without_status_keeps_the_type_specific_reason(
    exc: BaseException,
) -> None:
    assert (
        _incomplete_reason_for_exception(exc)
        == f"case_execution_error:{type(exc).__name__}"
    )


def test_provider_rejection_reaches_the_row_and_the_upstream_bucket(tmp_path) -> None:
    """End to end: runner raises 403 -> row reason -> manifest group."""

    def _reject(_prompt: str) -> str:
        raise RunnerError(OPENROUTER_403)

    executor = RegisteredCaseExecutor(
        subject=MockRunner("target", _reject),
        judge=MockRunner("judge", lambda _prompt: '{"verdict":"pass","score":1.0}'),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_estimated_judge_cost_usd=100.0),
    )
    row = _row()

    result = executor(
        row,
        SimpleNamespace(job_id="job-31907", agent_id="agent-1", seed=42),
    )

    assert result.incomplete_reason == "upstream_http_403"
    assert (
        scheduled_scan._incomplete_group(
            result.incomplete_reason, target_calls=result.target_calls
        )
        == "upstream_http"
    )

    evidence = json.loads(
        (tmp_path / "job-31907" / row["test_id"] / f"{row['case_id']}.json").read_text(
            encoding="utf-8"
        )
    )
    assert evidence["error"] == "upstream_http_403"
    assert evidence["error_detail"] == OPENROUTER_403

    # The manifest row is built from the attempt result (api.full_scan_executor
    # EvidenceAttemptRow), so it is ``incomplete_reason`` -- not the evidence
    # file's ``error_detail`` -- that the family summary reads.  This is the
    # count that read 2,234 rows of ``format`` on 2026-09-10.
    manifest_row = {
        "family": row["test_id"],
        "test_id": row["test_id"],
        "status": result.status,
        "incomplete_reason": result.incomplete_reason,
        "target_calls": result.target_calls,
        "verdict": result.verdict,
    }
    _, incomplete = scheduled_scan._zoo_family_summary([], [manifest_row])
    assert incomplete["upstream_http"] == 1
    assert incomplete["format"] == 0
