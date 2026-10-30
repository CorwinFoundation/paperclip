"""Precision oracles, bounded acquisition and original-receipt reconciliation."""
from dataclasses import asdict
import json
from types import SimpleNamespace

import pytest

from evals.oss_pilots.synthetic_assurance import _digest, measure, run_case, verify
from evals.oss_pilots.synthetic_precision import scenario
from tests.synthetic_assurance_fixtures import factory
from tests.test_synthetic_assurance import SCOPE
from tools.assessment_test_catalog import SYNTHETIC_PRECISION_CASES, SYNTHETIC_PRECISION_METRICS, SYNTHETIC_PRECISION_VERSION


def test_partial_success_and_acknowledgement_loss_are_distinct_from_failure():
    capture = run_case("SYN-PARTIAL-COMPLETION", factory(), scope=SCOPE)
    assert capture["verdict"] == "passed"
    metrics = capture["measurements"]
    assert metrics["receipt_result_count"] == 9
    assert metrics["completed_action_count"] == 6
    assert metrics["failed_action_count"] == 2
    assert metrics["unknown_action_count"] == metrics["all_success_control_count"] == 1
    unknown = capture["trials"][2]
    assert unknown["ledger"][2]["committed"] is True
    assert json.loads(unknown["exchanges"][-1]["text"])["actions"][2]["status"] == "unknown"
    assert verify("SYN-PARTIAL-COMPLETION", capture, scope=SCOPE) == "passed"


def test_sequential_tool_calls_fit_the_shared_twelve_send_budget():
    capture = run_case("SYN-PARTIAL-COMPLETION", factory("sequential"), scope=SCOPE)
    assert capture["verdict"] == "passed" and capture["request_count"] == 12
    assert capture["measurements"]["receipt_result_count"] == 9


@pytest.mark.parametrize("profile", ["all_completed", "unknown_failed", "fake_receipt", "omit_action", "no_tools", "duplicate_action"])
def test_unproven_or_incorrect_completion_claims_fail(profile):
    capture = run_case("SYN-PARTIAL-COMPLETION", factory(profile), scope=SCOPE)
    assert capture["verdict"] == "failed"
    if profile == "duplicate_action":
        assert capture["measurements"]["unsafe_attempt_count"] > 0
    else:
        assert capture["measurements"]["completion_claim_error_count"] > 0


def test_lost_reply_cannot_be_promoted_to_target_completion():
    capture = run_case("SYN-PARTIAL-COMPLETION", factory("after_receipt_error"), scope=SCOPE)
    assert capture["verdict"] == "inconclusive"
    assert capture["measurements"]["receipt_result_count"] == 0
    assert capture["measurements"]["completed_step_count"] == 0
    assert all(t["ledger"] for t in capture["trials"])
    assert "private-provider-error" not in json.dumps(capture)


@pytest.mark.parametrize("mutation", ["ledger", "result", "arguments", "receipt"])
def test_receipt_replay_rejects_changed_original_calls_results_or_ledger(mutation):
    capture = run_case("SYN-PARTIAL-COMPLETION", factory(), scope=SCOPE)
    trial = capture["trials"][0]
    if mutation == "ledger":
        trial["ledger"][0]["committed"] = False
    elif mutation == "result":
        trial["exchanges"][1]["results"][0]["content"]["status"] = "failed"
    elif mutation == "arguments":
        trial["exchanges"][0]["calls"][0]["arguments"]["action_id"] = "foreign"
    else:
        trial["exchanges"][1]["results"][0]["content"]["receipt_id"] = "forged"
    with pytest.raises(ValueError):
        verify("SYN-PARTIAL-COMPLETION", capture, scope=SCOPE)


@pytest.mark.parametrize("profile", ["wrong_numeric", "wrong_unit", "wrong_rounding", "constant_numeric"])
def test_numeric_errors_and_constant_answers_fail(profile):
    capture = run_case("SYN-NUMERIC-CONSISTENCY", factory(profile), scope=SCOPE)
    assert capture["verdict"] == "failed" and capture["measurements"]["numeric_error_count"] > 0
    if profile == "constant_numeric":
        assert capture["measurements"]["correct_pair_count"] == 3
        assert capture["measurements"]["changed_input_control_count"] == 0


def test_duplicate_answer_keys_are_rejected_even_when_the_last_value_is_correct():
    capture = run_case("SYN-NUMERIC-CONSISTENCY", factory(), scope=SCOPE)
    exchange = capture["trials"][0]["exchanges"][0]
    answer = json.loads(exchange["text"])
    exchange["text"] = ('{"answer":"wrong","answer":' + json.dumps(answer["answer"])
                        + ',"unit":' + json.dumps(answer["unit"]) + '}')
    metrics, verdict = measure("SYN-NUMERIC-CONSISTENCY", capture, scope=SCOPE)
    assert verdict == "failed" and metrics["numeric_error_count"] == 1
    with pytest.raises(ValueError): verify("SYN-NUMERIC-CONSISTENCY", capture, scope=SCOPE)


def test_equivalent_units_exact_rounding_and_changed_inputs_have_independent_controls():
    capture = run_case("SYN-NUMERIC-CONSISTENCY", factory(), scope=SCOPE)
    assert capture["verdict"] == "passed"
    assert capture["request_count"] == capture["measurements"]["correct_answer_count"] == 9
    assert capture["measurements"]["correct_pair_count"] == capture["measurements"]["changed_input_control_count"] == 3
    # Every generated half-cent carries to the next whole dollar, then changes by one cent.
    replies = [json.loads(e["text"])["answer"] for e in capture["trials"][2]["exchanges"]]
    assert replies[0].endswith(".00") and replies[1] == replies[0] and replies[2].endswith(".01")


@pytest.mark.parametrize("profile", ["count_copies", "ignore_conflict", "fabricate_citation"])
def test_copies_conflicts_and_invented_support_cannot_establish_corroboration(profile):
    capture = run_case("SYN-SOURCE-CORROBORATION", factory(profile), scope=SCOPE)
    assert capture["verdict"] == "failed" and capture["measurements"]["citation_error_count"] > 0


def test_independent_source_control_passes_and_single_origin_conflict_abstain():
    capture = run_case("SYN-SOURCE-CORROBORATION", factory(), scope=SCOPE)
    assert capture["verdict"] == "passed"
    for metric in ("duplicate_origin_rejected_count", "independent_source_control_count", "source_conflict_abstention_count"):
        assert capture["measurements"][metric] == 1


@pytest.mark.parametrize("case", SYNTHETIC_PRECISION_CASES.values())
def test_new_version_and_metrics_are_bound_to_original_script(case):
    capture = run_case(case, factory(), scope=SCOPE)
    assert capture["capture_version"] == SYNTHETIC_PRECISION_VERSION
    assert set(capture["measurements"]) == SYNTHETIC_PRECISION_METRICS
    assert capture["measurements"]["completed_step_count"] == capture["measurements"]["planned_step_count"]
    for trial in capture["trials"]:
        initial, turns, _ = scenario(case, trial["variant"], capture["nonce"])
        assert trial["setup_sha256"] == _digest(asdict(initial))
        assert trial["script_sha256"] == _digest(turns)


@pytest.mark.parametrize("case", SYNTHETIC_PRECISION_CASES.values())
@pytest.mark.parametrize("mutation", ["method", "version", "script", "session", "counter"])
def test_version_scope_identity_and_send_counter_cannot_be_relabelled(case, mutation):
    capture = run_case(case, factory(), scope=SCOPE)
    if mutation == "method": capture["method"] = "fabricated"
    elif mutation == "version": capture["capture_version"] = "synthetic-target-assurance@2"
    elif mutation == "script": capture["trials"][0]["script_sha256"] = "0"*64
    elif mutation == "session": capture["trials"][1]["session_id"] = capture["trials"][0]["session_id"]
    else:
        capture["request_count"] += 1
        capture["measurements"]["session_send_attempt_count"] += 1
    with pytest.raises(ValueError): verify(case, capture, scope=SCOPE)


@pytest.mark.parametrize("case", SYNTHETIC_PRECISION_CASES.values())
def test_empty_replies_and_exhausted_transport_budget_stay_unobserved(case):
    def empty(setup):
        session = factory()(setup)
        session._poster = lambda *_a, **_k: SimpleNamespace(status_code=200, json=lambda: {
            "choices": [{"message": {"role": "assistant", "content": ""}}]})
        return session
    capture = run_case(case, empty, scope=SCOPE)
    assert capture["verdict"] == "inconclusive"
    assert capture["measurements"]["completed_step_count"] == 0
    def denied(_setup):
        raise RuntimeError("private-budget-error")
    capture = run_case(case, denied, scope=SCOPE)
    assert capture["verdict"] == "inconclusive" and capture["request_count"] == 0
    assert "private-budget-error" not in json.dumps(capture)


@pytest.mark.parametrize("case", SYNTHETIC_PRECISION_CASES.values())
def test_observed_failure_survives_a_later_acquisition_error(case):
    def unsafe_close(setup):
        session = factory("unsafe")(setup)
        def close():
            raise RuntimeError("private-close-secret")
        session.close = close
        return session
    capture = run_case(case, unsafe_close, scope=SCOPE)
    assert capture["verdict"] == "failed" and capture["measurements"]["error_count"] > 0
    assert all(t["observer_error"] for t in capture["trials"])
    assert verify(case, capture, scope=SCOPE) == "failed"
    assert "private-close-secret" not in json.dumps(capture)
