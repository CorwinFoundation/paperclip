"""BEAAA-39653: frozen voice-channel roster and shared-runner wiring."""

from collections import Counter

from scanners.starter_judge import COULD_NOT_RUN, FAILED, PASSED, starter_pack_id_for_rows, starter_score, voice_speakability
from scanners.starter_voice_pack import (
    LEARNING_PORTAL_PERSONA,
    STARTER_VOICE_PACK_ID,
    STARTER_VOICE_TEST_COUNT,
    VOICE_CASES,
    VOICE_FAMILIES,
)


def test_voice_pack_has_founder_accepted_60_unique_cases_and_ten_families():
    assert STARTER_VOICE_PACK_ID == "starter-voice-50@1"
    assert STARTER_VOICE_TEST_COUNT == 60
    assert len(VOICE_CASES) == len({c.case_id for c in VOICE_CASES}) == 60
    assert len(VOICE_FAMILIES) == 10
    assert Counter(c.family_id for c in VOICE_CASES) == {
        "in_domain_normal": 10,
        "product_fabrication": 5,
        "asr_shaped_input": 5,
        "multilingual_guardrails": 15,
        "screen_share_readback": 5,
        "summary_poisoning": 5,
        "goals_data_collection": 5,
        "human_disclosure": 5,
        "abuse_toward_agent": 5,
    }


def test_every_case_carries_the_learning_portal_deployment_persona():
    assert all(c.prompt.startswith(LEARNING_PORTAL_PERSONA + "\n\nUser: ") for c in VOICE_CASES)
    assert all(c.rubric_point_id and c.user_message for c in VOICE_CASES)


def test_canaries_are_unique_and_bound_to_their_own_prompt():
    canaries = [c.canary for c in VOICE_CASES if c.canary]
    assert len(canaries) == len(set(canaries))
    assert all(c.canary in c.prompt for c in VOICE_CASES if c.canary)


def test_human_disclosure_cases_have_deterministic_phrase_hint():
    rows = [c for c in VOICE_CASES if c.family_id == "human_disclosure"]
    assert rows and all("ai" in c.must_include for c in rows)


def test_speakability_is_derived_and_fail_closed():
    assert voice_speakability(None)[0] == COULD_NOT_RUN
    assert voice_speakability("A short spoken answer.")[0] == PASSED
    assert voice_speakability("Read https://example.test/docs")[0] == FAILED
    assert voice_speakability("Your code is 12345678")[0] == FAILED
    assert voice_speakability("Please confirm whether the code is 12345678.")[0] == PASSED


def test_voice_rows_retain_pack_identity():
    assert starter_pack_id_for_rows([{"pack_id": STARTER_VOICE_PACK_ID}]) == STARTER_VOICE_PACK_ID
    summary = starter_score([
        {"pack_id": STARTER_VOICE_PACK_ID, "family": "in_domain_normal", "verdict": PASSED}
    ])
    assert summary["score"] is None
    assert summary["passed"] == 1


def test_shared_runner_and_scan_path_name_voice_pack():
    from api import scan_handlers
    from scanners import customer_agent_walker, live_probe_orchestrator

    assert STARTER_VOICE_PACK_ID in scan_handlers._STARTER_PACK_IDS
    assert scan_handlers._STARTER_PACK_TEST_COUNTS[STARTER_VOICE_PACK_ID] == 60
    assert scan_handlers._effective_starter_pack_id(STARTER_VOICE_PACK_ID) == STARTER_VOICE_PACK_ID
    assert len(customer_agent_walker._JUDGED_STARTER_CASES[STARTER_VOICE_PACK_ID]) == 60
    assert STARTER_VOICE_PACK_ID in live_probe_orchestrator._JUDGED_STARTER_PACK_IDS


def test_shared_walker_dispatches_60_unique_messages_and_records_derived_speakability(monkeypatch):
    from scanners import customer_agent_walker

    class Judge:
        def complete(self, **_kwargs):
            return '{"verdict":"passed","reason":"safe"}'

    class Runner:
        pack_id = STARTER_VOICE_PACK_ID
        operational_scan_deadline_seconds = None
        case_progress_callback = None

        def __init__(self):
            self.prompts = []
            self.rows = []

        def complete(self, prompt):
            self.prompts.append(prompt)
            return "I am an AI assistant. Here is a concise answer."

        def record_starter_verdict(self, **row):
            self.rows.append(row)

    monkeypatch.setattr(customer_agent_walker, "judge_client_from_env", lambda: Judge())
    runner = Runner()
    emitted = list(customer_agent_walker.customer_agent_probe_run_fn(runner))
    assert len(emitted) == len(runner.prompts) == 60
    assert len(set(runner.prompts)) == 60
    assert len(runner.rows) == 75
    assert Counter(row["family"] for row in runner.rows)["speakability"] == 15
