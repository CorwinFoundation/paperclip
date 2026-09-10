from types import SimpleNamespace

from scripts.scheduled_scan import _family_summary
from tools.canonical_family_target_runner import REGISTRY_VERSION, _terminal_verdict
from tools.batch_a_isolated_target_runner import BatchAIsolatedTargetRunner, DevelopmentTarget


def _refusal():
    return {
        "family_id": "family",
        "registry_version": REGISTRY_VERSION,
        "runner_path": "module:runner",
        "rubric_point_id": "P3.2",
        "verdict": "edge_refusal",
        "customer_data": False,
        "score_activation": False,
    }


def test_canonical_refusal_is_untested_not_fail():
    row = {"family_id": "family", "runner_path": "module:runner", "rubric_point_id": "P3.2", "required_cases": 1}
    assert _terminal_verdict(row, 200, _refusal()) == "untested"


def test_scheduled_family_excludes_refusal_from_pass_and_fail():
    families, incomplete = _family_summary([], [{
        "test_id": "family", "status": "complete", "verdict": "edge_refusal", "target_calls": 1,
    }])
    assert families["family"]["pass"] == 0
    assert families["family"]["fail"] == 0
    assert families["family"]["untested"] == 1
    assert sum(incomplete.values()) == 0


def test_isolated_family_with_only_refusals_remains_untested(tmp_path, monkeypatch):
    runner = BatchAIsolatedTargetRunner(DevelopmentTarget("https://example.test", "fixture", "TEST_TOKEN"), tmp_path)
    monkeypatch.setattr("tools.batch_a_isolated_target_runner._require_pinned_worker", lambda _: "1")
    monkeypatch.setattr("tools.batch_a_isolated_target_runner._load_cases", lambda _: [("case-1", {})])
    monkeypatch.setattr(runner, "_run_case", lambda *args: {
        "case_id": "case-1", "verdict": "edge_refusal", "refusing_party": "cloudflare", "reason": "blocked",
    })
    result = runner.run_family("garak")
    assert result["verdict"] == "untested"
    assert result["untested_coverage"]["count"] == 1
