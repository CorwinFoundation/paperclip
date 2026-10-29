"""Actual target replies, temporal controls and replayable multi-turn evidence."""
from contextlib import contextmanager
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest

from evals.oss_pilots.agent_boundary import SessionSetup
from evals.oss_pilots.synthetic_assurance import _leaked, _values, run_case, verify
from tests.synthetic_assurance_fixtures import factory
from tests.test_standard_requirement_reports import executor
from tests.test_synthetic_assurance import SCOPE
from tools.assessment_test_catalog import (
    SYNTHETIC_DIALOGUE_CASES, SYNTHETIC_DIALOGUE_VERSION, SYNTHETIC_LEGACY_CASES,
    SYNTHETIC_METRICS, SYNTHETIC_VERSION, SYNTHETIC_VERSIONS,
)
from tools.operational_scan import registry_rows
from tools.registered_case_executor import ScanBudget


def test_short_disclosures_accumulate_across_user_turns_and_encodings():
    capture = run_case("SYN-CUMULATIVE-DISCLOSURE",factory("small_pieces"),scope=SCOPE)
    assert capture["verdict"]=="failed"
    assert capture["measurements"]["cumulative_leak_count"]==3
    assert capture["measurements"]["leaked_character_count"]==24
    assert capture["measurements"]["public_control_pass_count"]==9
    private = _values(capture["nonce"])[3]
    # The extra evidence is genuinely incremental to the old per-reply detector.
    assert all(not _leaked(e["text"],private) for t in capture["trials"] for e in t["exchanges"])
    assert verify("SYN-CUMULATIVE-DISCLOSURE",capture,scope=SCOPE)=="failed"


@pytest.mark.parametrize("profile",["pieces_then_error","partial_once","tool_piece"])
def test_observed_partial_disclosure_or_unauthorized_attempt_survives_interruption(profile):
    capture = run_case("SYN-CUMULATIVE-DISCLOSURE",factory(profile),scope=SCOPE)
    assert capture["verdict"]=="failed" and capture["request_count"]<=12
    assert capture["measurements"]["private_marker_leak_count"]>0
    if profile=="pieces_then_error":
        assert capture["measurements"]["completed_scenario_count"]==0
        assert capture["measurements"]["cumulative_leak_count"]==3
    if profile=="partial_once":
        assert capture["measurements"]["cumulative_leak_count"]==0
        assert capture["measurements"]["leaked_character_count"]==8
    assert "private-provider-error" not in json.dumps(capture)


def test_public_marker_and_request_text_are_not_counted_as_private_disclosure():
    capture = run_case("SYN-CUMULATIVE-DISCLOSURE",factory(),scope=SCOPE)
    assert capture["verdict"]=="passed"
    assert capture["measurements"]["leaked_character_count"]==0
    assert capture["measurements"]["public_control_pass_count"]==9


@pytest.mark.parametrize("case",SYNTHETIC_DIALOGUE_CASES.values())
def test_empty_observed_replies_remain_inconclusive(case):
    def empty_factory(setup):
        session=factory()(setup)
        session._poster=lambda *_args,**_kwargs: SimpleNamespace(status_code=200,json=lambda:{
            "choices":[{"message":{"role":"assistant","content":""}}]})
        return session
    capture=run_case(case,empty_factory,scope=SCOPE)
    assert capture["verdict"]=="inconclusive"
    assert capture["measurements"]["completed_step_count"]==0


@pytest.mark.parametrize("case",SYNTHETIC_DIALOGUE_CASES.values())
def test_completed_capture_cannot_hide_unobserved_send_attempts(case):
    capture=run_case(case,factory(),scope=SCOPE)
    capture["request_count"]+=1
    capture["measurements"]["session_send_attempt_count"]+=1
    with pytest.raises(ValueError,match="acquisition errors"):
        verify(case,capture,scope=SCOPE)


@pytest.mark.parametrize("profile",["stale","untrusted_override"])
def test_stale_answers_and_authority_claimed_inside_source_text_fail(profile):
    capture = run_case("SYN-SOURCE-REVISION",factory(profile),scope=SCOPE)
    assert capture["verdict"]=="failed"
    assert capture["measurements"]["citation_error_count"]>0


def test_corrections_withdrawals_conflicts_and_untrusted_override_controls_complete():
    capture = run_case("SYN-SOURCE-REVISION",factory(),scope=SCOPE)
    metrics = capture["measurements"]
    assert capture["verdict"]=="passed" and metrics["completed_step_count"]==9
    assert metrics["updated_answer_count"]==metrics["withdrawal_abstention_count"]==metrics["conflict_abstention_count"]==1
    assert metrics["untrusted_override_rejected_count"]==3


def test_constant_answers_cannot_pass_changed_fact_controls_even_if_pairs_agree():
    capture = run_case("SYN-FACT-TRANSFORMATION",factory("constant_answer"),scope=SCOPE)
    assert capture["verdict"]=="failed"
    assert capture["measurements"]["correct_pair_count"]==3
    assert capture["measurements"]["changed_fact_control_count"]==0


def test_distractors_cannot_be_substituted_for_the_requested_record():
    capture = run_case("SYN-FACT-TRANSFORMATION",factory("first_record"),scope=SCOPE)
    assert capture["verdict"]=="failed"


def test_injected_citations_fail_while_benign_instruction_like_text_stays_useful():
    capture = run_case("SYN-CITATION-INJECTION",factory("follow_injection"),scope=SCOPE)
    assert capture["verdict"]=="failed"
    assert capture["measurements"]["hostile_source_control_count"]==0
    assert capture["measurements"]["benign_source_control_count"]==1
    good = run_case("SYN-CITATION-INJECTION",factory(),scope=SCOPE)
    assert good["verdict"]=="passed" and good["measurements"]["hostile_source_control_count"]==2
    refused = run_case("SYN-CITATION-INJECTION",factory("refuse_source"),scope=SCOPE)
    assert refused["verdict"]=="failed" and refused["measurements"]["benign_source_control_count"]==0


@pytest.mark.parametrize("case",SYNTHETIC_DIALOGUE_CASES.values())
@pytest.mark.parametrize("change",["script","message","version","method","session"])
def test_new_capture_replay_rejects_changed_script_version_method_or_session(case,change):
    capture = run_case(case,factory(),scope=SCOPE)
    if change=="script":capture["trials"][0]["script_sha256"]="0"*64
    elif change=="message":capture["trials"][0]["exchanges"][0]["message"]="Different task"
    elif change=="version":capture["capture_version"]=SYNTHETIC_VERSION
    elif change=="method":capture["method"]="Invented method"
    else:capture["trials"][1]["session_id"]=capture["trials"][0]["session_id"]
    with pytest.raises(ValueError):verify(case,capture,scope=SCOPE)


@pytest.mark.parametrize("case",SYNTHETIC_LEGACY_CASES.values())
def test_original_v1_contract_and_measurements_stay_available(case):
    capture = run_case(case,factory(),scope=SCOPE)
    assert capture["capture_version"]==SYNTHETIC_VERSION=="synthetic-target-assurance@1"
    assert set(capture["measurements"])==SYNTHETIC_METRICS
    assert verify(case,deepcopy(capture),scope=SCOPE)=="passed"


@pytest.mark.parametrize("family,case",SYNTHETIC_DIALOGUE_CASES.items())
def test_production_http_factory_preserves_user_history_auth_and_actual_send_budget(tmp_path,monkeypatch,family,case):
    calls,options,sessions = [],[],{}
    def handle(wire):
        body=json.loads(wire.content)
        assert str(wire.url)=="https://target.example/v1/chat/completions"
        assert wire.headers["X-Test-Key"]=="inert-test-token"
        assert body["max_tokens"]==256 and body["temperature"]==0
        messages=body["messages"]
        assert messages[0]["content"].startswith("Configured customer policy")
        user=next(m["content"] for m in messages if m["role"]=="user")
        key=(messages[0]["content"],user)
        if len(messages)==2:
            sessions[key]=factory()(SessionSetup(key[0],user,tuple(body.get("tools",[]))))
        response=sessions[key]._poster(str(wire.url),json=body)
        calls.append(messages)
        return httpx.Response(200,stream=httpx.ByteStream(json.dumps(response.json()).encode()),
                              headers={"Content-Type":"application/json"})
    @contextmanager
    def scoped(*_args,**_kwargs):
        def client(**kw):
            options.append(kw)
            return httpx.Client(transport=httpx.MockTransport(handle),**kw)
        yield SimpleNamespace(client=client)
    monkeypatch.setattr("harness.customer_runner.ScopedEgressAllowlist",scoped)
    config={"transport":"openai_chat","model":"normal-model","system_prompt":"Configured customer policy",
            "auth":{"type":"custom_header","header_name":"X-Test-Key"}}
    worker=executor(tmp_path,budget=ScanBudget(max_target_calls=12,max_judge_calls=0),
                    operational_target=("https://target.example/v1/chat/completions","inert-test-token",config))
    row=next(r for r in registry_rows() if r["test_id"]==family)
    outcome=worker(row,SimpleNamespace(job_id="job-A",scan_id="scan-A",agent_id="agent-A",seed=42))
    assert outcome.verdict=="pass" and outcome.numeric_result is None and outcome.judge_calls==0
    assert outcome.target_calls==worker.ledger.target_calls==len(calls)==(3 if case=="SYN-CITATION-INJECTION" else 9)
    assert all(o["timeout"]==30 and o["follow_redirects"] is False for o in options)
    body=json.loads(next(tmp_path.rglob(case+".json")).read_bytes())
    assert body["version_pin"]==body["capture"]["capture_version"]==SYNTHETIC_VERSIONS[case]==SYNTHETIC_DIALOGUE_VERSION
    if case!="SYN-CITATION-INJECTION":
        assert any(len(m)>2 for m in calls)  # The actual configured transport carries prior turns.
