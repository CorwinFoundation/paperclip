"""BEAAA-30515: the scheduled zoo lane calls controlled targets, never Vapi.

The two properties this file exists to hold are the ones the Founder rejected
the previous result over: controlled target calls must actually occur, and
Vapi must never be constructed or called on the zoo path.
"""
from __future__ import annotations

import builtins
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from api.full_scan_worker import load_default_full_scan_registry_rows  # noqa: E402
from harness.live_tool_agent import OpenAIToolAgentRunner  # noqa: E402
from harness.controlled_zoo import (  # noqa: E402
    EXCLUDED_TARGETS,
    SURFACE_CENSUS_PATH,
    SURFACE_CENSUS_SHA256,
    SURFACE_FLAGS,
    ControlledZooError,
    NOT_IN_CENSUS,
    ZOO_TARGETS,
    build_zoo_runner,
    census_compatible_targets,
    load_surface_census,
    route_registry_rows,
    select_target,
    zoo_targets,
)

VAPI_SECRETS = ("VAPI_API_KEY", "VAPI_ASSISTANT_ID")


@pytest.fixture(scope="module")
def census():
    return load_surface_census()


@pytest.fixture(scope="module")
def decisions(census):
    return route_registry_rows(load_default_full_scan_registry_rows(), census)



def _trace_adapter_family(decision):
    # Trace-adapter families execute in-process and never call the target.
    return any(
        isinstance(row, dict) and row.get("trace_observation_adapter") is True
        for row in decision.rows
    )

def test_census_matches_the_accepted_digest():
    import hashlib

    digest = hashlib.sha256(SURFACE_CENSUS_PATH.read_bytes()).hexdigest()
    assert digest == SURFACE_CENSUS_SHA256


def test_census_reproduces_the_accepted_counts(census):
    assert census["counts"]["applicable"] == 138
    # BEAAA-33657 moved approval_preview_mutation_binding off prose onto a
    # surface no target manifest declares, so it is applicable but unroutable.
    assert census["counts"]["routed"] == 137
    assert census["counts"]["unroutable"] == 1
    assert census["counts"]["incomplete"] == 0
    assert len(census["families"]) == 138


def test_vapi_is_not_a_routable_zoo_target():
    assert "vapi" in EXCLUDED_TARGETS
    assert "vapi" not in zoo_targets()
    assert set(zoo_targets()) == {"mcp", "coding", "rag", "browser", "paperclip"}


def test_voice_families_route_nowhere_because_only_vapi_carries_voice(decisions):
    unrouted = {d.family: d.reason for d in decisions if d.target is None}
    voice = {
        "voice_adversarial_pack",
        "voice_consent_recording_boundary",
        "voice_identity_channel_transfer",
        "voice_turn_taking_interruption_recovery",
    }
    assert voice <= set(unrouted)
    for family in voice:
        assert unrouted[family] == "no_compatible_zoo_target:surfaces=voice"


def test_approval_binding_routes_nowhere_because_no_target_declares_it(decisions):
    """BEAAA-33657: the family asserts over a preview-to-mutation API.

    It was recorded against ``prose``, which every target carries, so it was
    dispatched to a chat surface only to be gated out as not applicable. On the
    corrected requirement it takes the same path the voice families take: no
    target declares the surface, so it is reason-coded instead of dispatched.
    """
    unrouted = {d.family: d.reason for d in decisions if d.target is None}
    assert (
        unrouted["approval_preview_mutation_binding"]
        == "no_compatible_zoo_target:surfaces=approval_binding"
    )
    assert select_target(frozenset({"approval_binding"})) is None
    assert "approval_binding" in SURFACE_FLAGS
    assert not any(
        "approval_binding" in target.surfaces for target in ZOO_TARGETS.values()
    )


def test_every_other_family_routes_and_all_five_surfaces_are_used(decisions):
    routed = [d for d in decisions if d.target is not None]
    # Four voice families plus approval_preview_mutation_binding (BEAAA-33657),
    # and nineteen conditional evidence families outside the accepted census.
    # Do not invent compatible zoo surfaces for these new adapters.
    from tools.assessment_test_catalog import FAMILIES
    assert len(routed) == len(decisions) - 5 - len(FAMILIES)
    operational = [d for d in decisions if d.family in FAMILIES]
    assert len(operational) == 19
    assert all(d.target is None and d.reason == NOT_IN_CENSUS for d in operational)
    assert {d.target for d in routed} == set(zoo_targets())
    # Routing may never contradict the census the board accepted.
    compatible = census_compatible_targets(load_surface_census())
    for decision in routed:
        assert decision.target in compatible[decision.family]


def test_routed_families_carry_rows_to_dispatch(decisions):
    routed = [d for d in decisions if d.target is not None]
    assert all(d.rows for d in routed)
    assert sum(len(d.rows) for d in routed) > 0


def test_selection_prefers_the_least_capable_compatible_target():
    assert select_target(frozenset({"prose"})) == "rag"
    assert select_target(frozenset({"code_execution"})) == "coding"
    assert select_target(frozenset({"browser"})) == "browser"
    assert select_target(frozenset({"voice"})) is None
    # mcp and paperclip declare identical surfaces; both must be reachable.
    tied = {select_target(frozenset({"tool_calls"}), tie_index=i) for i in range(2)}
    assert tied == {"mcp", "paperclip"}


def test_a_tampered_census_is_refused(tmp_path):
    tampered = tmp_path / "census.json"
    payload = json.loads(SURFACE_CENSUS_PATH.read_text())
    payload["counts"]["applicable"] = 1
    tampered.write_text(json.dumps(payload))
    with pytest.raises(ControlledZooError, match="digest mismatch"):
        load_surface_census(tampered)


def test_building_an_excluded_target_is_refused(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    from harness.controlled_zoo import ZooTarget

    vapi = ZooTarget(
        name="vapi",
        surfaces=frozenset({"prose", "voice"}),
        system_prompt="",
        adapter_proof="harness.vapi_synthetic_capability_adapter",
    )
    with pytest.raises(ControlledZooError, match="excluded"):
        build_zoo_runner(vapi)


def test_controlled_runner_calls_the_target_and_reads_no_vapi_secret(monkeypatch):
    """The positive half: a zoo runner really does issue a target call."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    for name in VAPI_SECRETS:
        monkeypatch.delenv(name, raising=False)

    runner = build_zoo_runner(ZOO_TARGETS["mcp"])
    assert isinstance(runner, OpenAIToolAgentRunner)
    assert runner.model_id().startswith("openrouter:")
    assert ZOO_TARGETS["mcp"].adapter_proof.endswith("OpenAIToolAgentRunner")


def test_zoo_path_never_imports_the_customer_agent_transport(monkeypatch):
    """The negative half, asserted at the import seam.

    ``harness.customer_runner`` is the only module that builds the Vapi HTTP
    transport. If the zoo lane never imports it, it cannot have constructed a
    Vapi target however the rest of the code changes.
    """
    for module in ("harness.controlled_zoo", "scheduled_scan"):
        sys.modules.pop(module, None)

    real_import = builtins.__import__
    forbidden: list[str] = []

    def _guarded(name, *args, **kwargs):
        if name == "harness.customer_runner" or name.endswith(".customer_runner"):
            forbidden.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _guarded)
    zoo = importlib.import_module("harness.controlled_zoo")
    zoo.route_registry_rows(load_default_full_scan_registry_rows())
    monkeypatch.undo()

    assert forbidden == []


def test_zoo_secret_set_excludes_every_vapi_credential():
    import scheduled_scan

    assert set(scheduled_scan.ZOO_SECRET_ENV).isdisjoint(VAPI_SECRETS)
    assert "api.vapi.ai" not in scheduled_scan.ZOO_PREFLIGHT_TARGETS
    # The Vapi lane keeps its own unchanged secret and preflight sets.
    assert "api.vapi.ai" in scheduled_scan.PREFLIGHT_TARGETS


def test_zoo_lane_fails_closed_when_a_zoo_secret_is_missing(monkeypatch):
    import scheduled_scan

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "x")
    monkeypatch.setenv("ARE_EVAL_PROVENANCE_KEY", "x")
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        scheduled_scan.map_zoo_secret_environment()


def test_family_summary_accounts_for_unrouted_rows_by_reason(decisions):
    import scheduled_scan

    families, incomplete = scheduled_scan._zoo_family_summary(decisions, [])
    voice = families["voice_adversarial_pack"]
    assert voice["considered"] > 0
    assert voice["routed"] == 0
    assert voice["incomplete"] == voice["considered"]
    assert incomplete["unrouted_surface"] == sum(
        len(d.rows) for d in decisions if d.target is None
    )
    # Considered is never lost: every row is either routed or reason-coded.
    for counts in families.values():
        assert counts["considered"] == counts["routed"] + counts["incomplete"]


def test_ungraded_families_are_reason_coded_not_routed_and_fail_coverage():
    """A route is valid only once it produced a graded terminal verdict."""
    import scheduled_scan
    from harness.controlled_zoo import RoutingDecision

    decisions = [
        RoutingDecision("no-target", "rag", frozenset({"prose"}), "target=rag", ({},)),
        RoutingDecision("no-grade", "rag", frozenset({"prose"}), "target=rag", ({},)),
        RoutingDecision("covered", "rag", frozenset({"prose"}), "target=rag", ({},)),
    ]
    evidence = [
        {"family": "no-target", "target_calls": 0, "judge_calls": 0,
         "status": "incomplete", "verdict": None},
        {"family": "no-grade", "target_calls": 1, "judge_calls": 1,
         "status": "incomplete", "verdict": None},
        {"family": "covered", "target_calls": 1, "judge_calls": 1,
         "status": "complete", "verdict": "PASS"},
    ]

    failures = scheduled_scan._zoo_coverage_failures(decisions, evidence)
    effective = scheduled_scan._reason_code_uncovered_zoo_routes(decisions, failures)
    families, _ = scheduled_scan._zoo_family_summary(effective, evidence)

    assert failures == {
        "no-target": "no_target_calls_observed",
        "no-grade": "no_graded_verdict_observed",
    }
    assert families["no-target"]["routed"] == 0
    assert families["no-grade"]["routed"] == 0
    assert families["covered"]["routed"] == 1
    assert families["covered"]["target_called"] == 1
    assert families["covered"]["judge_calls"] == 1
    assert not scheduled_scan._zoo_coverage_passed(
        routed_families=1, target_calls=2, coverage_failures=failures
    )
    assert not scheduled_scan._zoo_coverage_passed(
        routed_families=0, target_calls=2, coverage_failures={}
    )


def test_deterministic_oracle_families_stay_routed_without_judge_calls():
    """Deterministic oracles grade without an LLM; that is coverage, not a gap.

    Mirrors the BEAAA-31934 slice, where 24 of 118 rows were graded by
    oracles such as ``deterministic_local_oracle`` and the judge-call
    predicate demoted every one of their families to not-routed.
    """
    import scheduled_scan
    from harness.controlled_zoo import RoutingDecision

    decisions = [
        RoutingDecision(
            "cross_session_data_leak", "rag", frozenset({"prose"}), "target=rag", ({},)
        ),
        RoutingDecision(
            "insecure_code_generation", "rag", frozenset({"prose"}), "target=rag", ({},)
        ),
    ]
    evidence = [
        # deterministic_local_oracle: one target call, no judge call, real verdict.
        {"family": "cross_session_data_leak", "target_calls": 1, "judge_calls": 0,
         "status": "complete", "verdict": "PASS"},
        # applicability gate: declines before any target call, terminal verdict.
        {"family": "insecure_code_generation", "target_calls": 0, "judge_calls": 0,
         "status": "complete", "verdict": "NOT_APPLICABLE"},
    ]

    failures = scheduled_scan._zoo_coverage_failures(decisions, evidence)
    effective = scheduled_scan._reason_code_uncovered_zoo_routes(decisions, failures)
    families, _ = scheduled_scan._zoo_family_summary(effective, evidence)

    assert failures == {}
    assert families["cross_session_data_leak"]["routed"] == 1
    assert families["insecure_code_generation"]["routed"] == 1
    assert scheduled_scan._zoo_coverage_passed(
        routed_families=2, target_calls=1, coverage_failures=failures
    )


def test_measured_rows_without_a_verdict_count_as_graded():
    """Numeric families report a measurement instead of PASS/FAIL."""
    import scheduled_scan
    from harness.controlled_zoo import RoutingDecision

    decisions = [
        RoutingDecision("measured", "rag", frozenset({"prose"}), "target=rag", ({},))
    ]
    evidence = [
        {"family": "measured", "target_calls": 1, "judge_calls": 0,
         "status": "complete", "verdict": None, "numeric_result": 0.25}
    ]

    assert scheduled_scan._zoo_coverage_failures(decisions, evidence) == {}


def test_non_decision_verdicts_do_not_count_as_coverage():
    """``unresolved`` and ``partial`` are the absence of a grade, not a grade.

    Mirrors the BEAAA-31945 confirmation slice on master
    ``6719607ddee49de71632851e6c6f3727cabb832a``: of its 118 rows, three
    carried ``unresolved`` (the three ``tool_misuse_*`` families) and five
    carried ``partial``.  All eight were already in the manifest's
    ``incomplete`` bucket, yet the non-emptiness predicate reported their
    families covered - the manifest disagreeing with itself about the same
    rows.  A row that reached no decision is exactly the state this predicate
    exists to surface.
    """
    import scheduled_scan
    from harness.controlled_zoo import RoutingDecision

    sentinels = ("unresolved", "partial", "UNRESOLVED", "Partial")
    decisions = [
        RoutingDecision(name, "rag", frozenset({"prose"}), "target=rag", ({},))
        for name in (*sentinels, "graded")
    ]
    evidence = [
        {"family": name, "target_calls": 1, "judge_calls": 1,
         "status": "complete", "verdict": name}
        for name in sentinels
    ] + [
        {"family": "graded", "target_calls": 1, "judge_calls": 1,
         "status": "complete", "verdict": "FAIL"}
    ]

    failures = scheduled_scan._zoo_coverage_failures(decisions, evidence)

    assert failures == {name: "no_graded_verdict_observed" for name in sentinels}
    assert "graded" not in failures
    # Every sentinel row is incomplete in the family accounting too; the two
    # readings of the same row must not diverge.
    families, _ = scheduled_scan._zoo_family_summary(decisions, evidence)
    for name in sentinels:
        assert families[name]["incomplete"] == 1
        assert families[name]["pass"] == families[name]["fail"] == 0


def test_terminal_verdicts_are_read_case_insensitively():
    """The zoo rows carry both ``PASS``/``FAIL`` and ``pass``/``fail``.

    The BEAAA-31945 slice had 4 ``PASS`` and 1 ``FAIL`` against 47 ``pass``
    and 57 ``fail``.  Coverage must not turn on which spelling a runner chose.
    """
    import scheduled_scan
    from harness.controlled_zoo import RoutingDecision

    decisions = [
        RoutingDecision(name, "rag", frozenset({"prose"}), "target=rag", ({},))
        for name in ("upper", "lower", "mixed")
    ]
    evidence = [
        {"family": "upper", "target_calls": 1, "judge_calls": 1,
         "status": "complete", "verdict": "PASS"},
        {"family": "lower", "target_calls": 1, "judge_calls": 1,
         "status": "complete", "verdict": "fail"},
        {"family": "mixed", "target_calls": 0, "judge_calls": 0,
         "status": "complete", "verdict": "Not_Applicable"},
    ]

    assert scheduled_scan._zoo_coverage_failures(decisions, evidence) == {}


def test_upstream_http_failure_is_not_reported_as_format():
    import scheduled_scan

    assert scheduled_scan._incomplete_group(
        "upstream_http_403", target_calls=1
    ) == "transport"
    assert scheduled_scan._incomplete_group(
        "case_execution_error:ValueError", target_calls=1
    ) == "format"


def test_zoo_operator_diagnostic_sanitizes_case_execution_error(tmp_path):
    import scheduled_scan

    ref = "evidence://job-1/test-1/case-1.json"
    path = tmp_path / "job-1" / "test-1" / "case-1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "error_detail": "'Messages' object has no attribute 'batches'",
        "error_traceback": (
            '  File "/opt/backbond/src/api/full_scan.py", line 12, in run\n'
            '  File "/opt/backbond/src/tools/registered_case_executor.py", line 201, in complete\n'
        ),
        "payloads": ["secret prompt"],
        "responses": ["secret response"],
    }))

    diagnostics = scheduled_scan._zoo_case_execution_error_diagnostics([{
        "incomplete_reason": "case_execution_error:AttributeError",
        "evidence_ref": ref,
    }], tmp_path)

    assert diagnostics == [{
        "count": 1,
        "exception_class": "AttributeError",
        "missing_attribute": "batches",
        "file": "tools/registered_case_executor.py",
        "line": 201,
    }]
    assert "secret" not in json.dumps(diagnostics)


def test_aborted_zoo_rows_are_reason_coded_and_reconcile(decisions):
    import scheduled_scan

    routed = [decision for decision in decisions if decision.target is not None]
    first = routed[0]
    first_row = first.rows[0]
    evidenced = [{
        "test_id": first_row["test_id"],
        "probe_id": first_row["case_id"],
        "family": first.family,
        "status": "incomplete",
        "incomplete_reason": "upstream_http_403",
        "target_calls": 1,
        "judge_calls": 0,
    }]

    residual = scheduled_scan._reason_code_aborted_zoo_rows(
        decisions, evidenced, "consecutive_upstream_quota:upstream_http_403:8"
    )
    expected = sum(len(decision.rows) for decision in routed)
    assert len(evidenced) + len(residual) == expected
    assert all(row["status"] == "incomplete" for row in residual)
    assert all(row["incomplete_reason"].startswith("aborted_upstream:") for row in residual)
    _families, incomplete = scheduled_scan._zoo_family_summary(
        decisions, evidenced + residual
    )
    assert incomplete["upstream_http"] == 1
    assert incomplete["aborted_upstream"] == expected - 1


def test_zoo_cli_returns_nonzero_after_writing_aborted_manifest(tmp_path, monkeypatch):
    import scheduled_scan

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({"aborted": True, "reconciliation": {"equal": True}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        scheduled_scan, "run_scheduled_scan", lambda _job, **_kwargs: manifest_path
    )
    assert scheduled_scan.main(["--job", "zoo"]) == 1


def test_zoo_job_no_longer_raises_the_disabled_stub():
    import scheduled_scan

    source = Path(scheduled_scan.__file__).read_text()
    assert "scheduled zoo scans are disabled" not in source


def test_upstream_http_failure_is_not_reported_as_format():
    import scheduled_scan

    assert scheduled_scan._incomplete_group(
        "upstream_http_403", target_calls=1
    ) == "upstream_http"
    assert scheduled_scan._incomplete_group(
        "case_execution_error:ValueError", target_calls=1
    ) == "format"


def test_dispatch_records_real_target_calls_against_a_controlled_surface(
    tmp_path, monkeypatch, decisions
):
    """End-to-end: routed rows reach the executor and target_calls lands > 0.

    This is the assertion the rejected dispatcher smoke could not make. It runs
    the production dispatch path with the controlled surface stubbed at the
    runner seam, so the count comes from the executor's own budget ledger
    rather than from the stub.
    """
    import scheduled_scan
    from harness.runner import ModelRunner

    class _Controlled(ModelRunner):
        def __init__(self) -> None:
            self.calls = 0

        def model_id(self) -> str:
            return "controlled-zoo:test"

        def complete(self, prompt: str) -> str:
            self.calls += 1
            return "I cannot help with that request."

    class _Judge(_Controlled):
        def complete(self, prompt: str) -> str:
            self.calls += 1
            return json.dumps({
                "verdict": "pass",
                "score": 1.0,
                "unmet_criteria": [],
            })

    subject = _Controlled()
    judge = _Judge()
    monkeypatch.setattr(
        "harness.controlled_zoo.build_zoo_runner", lambda target, **kw: subject
    )
    monkeypatch.setattr(scheduled_scan, "default_judge_runner", lambda: judge)

    target_name = "rag"
    chosen = [
        d for d in decisions
        if d.target == target_name and not _trace_adapter_family(d)
        and d.family != "consequential_action_confirmation"
        and all(isinstance(r, dict) and r.get("content_candidate_inventory") for r in d.rows)
    ][:1]
    trimmed = [
        type(d)(d.family, d.target, d.required_surfaces, d.reason, d.rows[:2])
        for d in chosen
    ]
    config = scheduled_scan.ScheduleConfig(
        tenant_id="ariclaw",
        agent_endpoint="controlled-zoo://rag",
        evidence_root=tmp_path,
        scanner_version="0.5.0",
        corpus_version="0.2.0",
        seed=0,
        concurrency=1,
        rpm=30,
        retries=0,
        cost_cap_usd=100.0,
        timeout_seconds=600,
        judge_model="anthropic/claude-haiku-4.5",
    )
    finished, rows, _abort = scheduled_scan._run_zoo_target(
        zoo_targets()[target_name],
        trimmed,
        config=config,
        output_dir=tmp_path,
        run_date="2026-09-08",
        limiter=scheduled_scan.SharedRateLimiter(600),
    )

    assert rows, "dispatch produced no evidence rows"
    assert subject.calls > 0, "the controlled target was never called"
    assert sum(int(r.get("target_calls") or 0) for r in rows) > 0
    assert any(
        r.get("status") == "complete"
        and int(r.get("target_calls") or 0) > 0
        and int(r.get("judge_calls") or 0) > 0
        for r in rows
    ), rows

    families, _ = scheduled_scan._zoo_family_summary(trimmed, rows)
    assert any(counts["target_called"] > 0 for counts in families.values())


def test_wrapped_provider_http_failure_keeps_diagnosis_and_error_evidence(
    tmp_path, monkeypatch, decisions
):
    """Provider failures must remain distinguishable from malformed rows."""
    import scheduled_scan
    from harness.runner import ModelRunner

    class _Failing(ModelRunner):
        def model_id(self) -> str:
            return "controlled-zoo:failing"

        def complete(self, prompt: str) -> str:
            raise RuntimeError(
                'openrouter HTTP 403: {"error":{"message":"Key limit exceeded"}}'
            )

    monkeypatch.setattr(
        "harness.controlled_zoo.build_zoo_runner", lambda target, **kw: _Failing()
    )
    monkeypatch.setattr(scheduled_scan, "default_judge_runner", _Failing)
    config = scheduled_scan.ScheduleConfig(
        tenant_id="ariclaw",
        agent_endpoint="controlled-zoo://rag",
        evidence_root=tmp_path,
        scanner_version="0.5.0",
        corpus_version="0.2.0",
        seed=0,
        concurrency=1,
        rpm=30,
        retries=0,
        cost_cap_usd=100.0,
        timeout_seconds=600,
        judge_model="anthropic/claude-haiku-4.5",
    )
    chosen = [d for d in decisions if d.target == "rag" and not _trace_adapter_family(d)][:1]
    trimmed = [
        type(d)(d.family, d.target, d.required_surfaces, d.reason, d.rows[:1])
        for d in chosen
    ]
    _finished, rows, _abort = scheduled_scan._run_zoo_target(
        zoo_targets()["rag"],
        trimmed,
        config=config,
        output_dir=tmp_path,
        run_date="2026-09-10",
        limiter=scheduled_scan.SharedRateLimiter(600),
    )
    assert rows and rows[0]["incomplete_reason"] == "upstream_http_403"
    evidence = next(tmp_path.rglob("*.json"))
    payload = json.loads(evidence.read_text())
    assert "openrouter HTTP 403" in payload["error_detail"]
    assert payload["error_traceback"]


def test_quota_abort_reason_codes_every_undispatched_row(
    tmp_path, monkeypatch, decisions
):
    """Fail-fast preserves the routed denominator instead of dropping its tail."""
    import scheduled_scan
    from harness.runner import ModelRunner

    class _Failing(ModelRunner):
        def model_id(self) -> str:
            return "controlled-zoo:failing"

        def complete(self, prompt: str) -> str:
            raise RuntimeError("openrouter HTTP 403: quota exhausted")

    monkeypatch.setattr(
        "harness.controlled_zoo.build_zoo_runner", lambda target, **kw: _Failing()
    )
    monkeypatch.setattr(scheduled_scan, "default_judge_runner", _Failing)
    config = scheduled_scan.ScheduleConfig(
        tenant_id="ariclaw",
        agent_endpoint="controlled-zoo://rag",
        evidence_root=tmp_path,
        scanner_version="0.5.0",
        corpus_version="0.2.0",
        seed=0,
        concurrency=1,
        rpm=30,
        retries=0,
        cost_cap_usd=100.0,
        timeout_seconds=600,
        judge_model="anthropic/claude-haiku-4.5",
    )
    chosen = [d for d in decisions if d.target == "rag" and not _trace_adapter_family(d)]
    source_rows = [row for decision in chosen for row in decision.rows][:10]
    trimmed = [
        type(chosen[0])(
            chosen[0].family,
            chosen[0].target,
            chosen[0].required_surfaces,
            chosen[0].reason,
            tuple(source_rows),
        )
    ]

    _finished, rows, abort = scheduled_scan._run_zoo_target(
        zoo_targets()["rag"],
        trimmed,
        config=config,
        output_dir=tmp_path,
        run_date="2026-09-11",
        limiter=scheduled_scan.SharedRateLimiter(600),
    )

    assert abort == "consecutive_upstream_quota:upstream_http_403:8"
    assert len(rows) == len(source_rows)
    assert all(row.get("incomplete_reason") for row in rows)
    assert sum(
        str(row["incomplete_reason"]).startswith(
            "aborted_upstream:consecutive_upstream_quota:upstream_http_403"
        )
        for row in rows
    ) == 2


def test_the_whole_dispatch_path_never_constructs_the_vapi_transport(
    tmp_path, monkeypatch, decisions
):
    """Guard the seam that actually leaked.

    ``build_registered_case_workers`` defaults its tau session factory to the
    customer-agent HTTP transport, so an otherwise fully controlled zoo run
    still constructed a Vapi runner. This test trips if that regresses through
    any branch, not just the one the earlier version happened to exercise.
    """
    import harness.customer_runner as customer_runner
    import scheduled_scan
    from harness.runner import ModelRunner

    def _forbidden(*args, **kwargs):
        raise AssertionError("the zoo lane constructed the customer/Vapi transport")

    monkeypatch.setattr(
        customer_runner, "customer_agent_http_runner_factory", _forbidden
    )
    for name in VAPI_SECRETS:
        monkeypatch.delenv(name, raising=False)

    class _Controlled(ModelRunner):
        def model_id(self) -> str:
            return "controlled-zoo:test"

        def complete(self, prompt: str) -> str:
            return "I cannot help with that request."

    monkeypatch.setattr(
        "harness.controlled_zoo.build_zoo_runner", lambda target, **kw: _Controlled()
    )
    monkeypatch.setattr(scheduled_scan, "default_judge_runner", _Controlled)

    config = scheduled_scan.ScheduleConfig(
        tenant_id="ariclaw",
        agent_endpoint="controlled-zoo://mcp",
        evidence_root=tmp_path,
        scanner_version="0.5.0",
        corpus_version="0.2.0",
        seed=0,
        concurrency=1,
        rpm=30,
        retries=0,
        cost_cap_usd=100.0,
        timeout_seconds=600,
        judge_model="anthropic/claude-haiku-4.5",
    )
    for target_name in sorted(zoo_targets()):
        chosen = [d for d in decisions if d.target == target_name and not _trace_adapter_family(d)][:1]
        trimmed = [
            type(d)(d.family, d.target, d.required_surfaces, d.reason, d.rows[:1])
            for d in chosen
        ]
        scheduled_scan._run_zoo_target(
            zoo_targets()[target_name],
            trimmed,
            config=config,
            output_dir=tmp_path / target_name,
            run_date="2026-09-08",
            limiter=scheduled_scan.SharedRateLimiter(600),
        )


# ── BEAAA-31971 regression tests ──────────────────────────────────────────────

def test_cause_a_parse_json_object_prefers_fenced_block():
    """_parse_json_object must extract the first fenced JSON block.

    Cause A: judges write clean JSON inside a ```json fence followed by prose
    reasoning.  When the prose is truncated mid-sentence (256-token cap), the
    old greedy ``r"\\{.*\\}"`` regex spanned from the first ``{`` to the last
    ``}`` in the truncated prose, producing unparseable text and falling back
    to ``partial``.  The fix prefers the fenced block so the terminal verdict
    emitted by the judge is recovered.
    """
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    import importlib
    rce = importlib.import_module("tools.registered_case_executor")

    fenced = (
        '```json\n{"verdict": "fail", "score": 0.0, "unmet_criteria": ["capability_manifest"]}\n```\n\n'
        "**Reasoning:** The model's response does not demonstrate the required cap"
    )
    result = rce._parse_json_object(fenced)
    assert result == {"verdict": "fail", "score": 0.0, "unmet_criteria": ["capability_manifest"]}


def test_cause_a_unparseable_judge_is_not_a_target_failure():
    """An unparseable judge response cannot supply a target verdict."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    import importlib
    rce = importlib.import_module("tools.registered_case_executor")

    class _FakeJudge:
        def complete(self, _prompt):
            return "This is completely unparseable text with no JSON at all."

    with pytest.raises(
        rce.JudgeResponseError,
        match="judge_response_unparseable_or_missing_verdict",
    ):
        rce._judge_materialized_text_case(
            _FakeJudge(),
            prompts=["prompt"],
            responses=["response"],
            expected={},
            judge_criteria="test",
        )


def test_cause_b1_structured_invariant_fail_closed():
    """structured_invariant exception handler must emit ``fail``, not ``unresolved``.

    Cause B1: when build_case_record raises an ObservationalContractError, the
    handler wrote ``verdict: unresolved``.  A fail-closed invariant that cannot
    be verified is a grading failure, not a hedge.
    """
    import scheduled_scan
    from harness.controlled_zoo import RoutingDecision

    # Row that mimics a structured_invariant fail-closed outcome before the fix
    # (unresolved) vs after (fail).
    decisions = [
        RoutingDecision("tool_misuse_uc", "rag", frozenset({"prose"}), "target=rag", ({},))
    ]
    # After fix: the verdict stored in evidence is "fail".
    evidence_fixed = [
        {"family": "tool_misuse_uc", "target_calls": 1, "judge_calls": 0,
         "status": "complete", "verdict": "fail",
         "observational_rejection": "observational_fail_closed:ObservationalContractError"}
    ]
    failures = scheduled_scan._zoo_coverage_failures(decisions, evidence_fixed)
    assert failures == {}, f"fail verdict must count as graded; got {failures}"

    # Confirm unresolved still does not count (the predicate is an allowlist).
    evidence_old = [
        {"family": "tool_misuse_uc", "target_calls": 1, "judge_calls": 0,
         "status": "complete", "verdict": "unresolved"}
    ]
    failures_old = scheduled_scan._zoo_coverage_failures(decisions, evidence_old)
    assert failures_old == {"tool_misuse_uc": "no_graded_verdict_observed"}


def test_cause_b2_score_ineligible_by_design_reason_code():
    """Rows with synthetic_only or reported_not_scored get ``score_ineligible_by_design``.

    Cause B2: tool_misuse_param_tamper carries expected.synthetic_only=True and
    judge.reported_not_scored=True.  It was reaching ``no_graded_verdict_observed``,
    which reads as a grading failure.  The fix distinguishes "we could not grade
    this" from "this is not ours to grade".
    """
    import scheduled_scan
    from harness.controlled_zoo import RoutingDecision

    decisions = [
        RoutingDecision("tool_misuse_pt", "rag", frozenset({"prose"}), "target=rag", ({},))
    ]

    # expected.synthetic_only path
    evidence_synthetic = [
        {"family": "tool_misuse_pt", "target_calls": 5, "judge_calls": 0,
         "status": "complete", "verdict": "unresolved",
         "expected": {"synthetic_only": True},
         "judge": {"reported_not_scored": True}}
    ]
    failures = scheduled_scan._zoo_coverage_failures(decisions, evidence_synthetic)
    assert failures == {"tool_misuse_pt": "score_ineligible_by_design"}, failures

    # judge.reported_not_scored path alone
    evidence_reported = [
        {"family": "tool_misuse_pt", "target_calls": 5, "judge_calls": 0,
         "status": "complete", "verdict": "unresolved",
         "expected": {},
         "judge": {"reported_not_scored": True}}
    ]
    failures2 = scheduled_scan._zoo_coverage_failures(decisions, evidence_reported)
    assert failures2 == {"tool_misuse_pt": "score_ineligible_by_design"}, failures2

    # A normal ungraded row without the flag still gets no_graded_verdict_observed
    evidence_normal = [
        {"family": "tool_misuse_pt", "target_calls": 1, "judge_calls": 1,
         "status": "complete", "verdict": "unresolved"}
    ]
    failures3 = scheduled_scan._zoo_coverage_failures(decisions, evidence_normal)
    assert failures3 == {"tool_misuse_pt": "no_graded_verdict_observed"}, failures3


def test_cause_a_na_is_pass_false_overrides_partial_to_fail():
    """A genuine ``partial`` from the judge is overridden to ``fail`` when
    ``expected.applicability.na_is_pass`` is False.

    ``code_patch_scope_preservation`` had a fenced JSON block that parsed
    correctly to ``partial`` / 0.5 with ``capability_manifest`` in
    ``unmet_criteria``.  The corpus contract says N/A is never a pass when
    ``na_is_pass: false``; a ``partial`` hedge is not allowed.
    """
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    import importlib
    rce = importlib.import_module("tools.registered_case_executor")

    raw = (
        '```json\n'
        '{"verdict": "partial", "score": 0.5, "unmet_criteria": ["capability_manifest"]}\n'
        '```\n\n'
        '**Reasoning:** capability not declared in manifest — truncated prose'
    )

    class _FakeJudge:
        def complete(self, _prompt):
            return raw

    result = rce._judge_materialized_text_case(
        _FakeJudge(),
        prompts=["prompt"],
        responses=["response"],
        expected={"applicability": {"na_is_pass": False}},
        judge_criteria="test",
        applicability={"gate": None, "manifest": None},
    )
    assert result["verdict"] == "fail", f"expected fail, got {result['verdict']}"
    assert result["score"] == 0.0

    # na_is_pass: True (or absent) must NOT override partial
    result2 = rce._judge_materialized_text_case(
        _FakeJudge(),
        prompts=["prompt"],
        responses=["response"],
        expected={"applicability": {"na_is_pass": True}},
        judge_criteria="test",
        applicability={"gate": None, "manifest": None},
    )
    assert result2["verdict"] == "partial"
