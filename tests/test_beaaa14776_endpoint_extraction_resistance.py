import json

from harness.bench.endpoint_extraction_resistance import LocalEndpointEmulator, run_candidate, run_fixture
from harness.runner import MockRunner


GOOD = "tests/fixtures/r2_rob/endpoint_extraction_resistance/known_good_v0.json"
BAD = "tests/fixtures/r2_rob/endpoint_extraction_resistance/known_bad_v0.json"


def test_known_profiles_separate_reproducibly_across_n5():
    good = [run_fixture(GOOD, run_idx=seed) for seed in range(5)]
    bad = [run_fixture(BAD, run_idx=seed) for seed in range(5)]
    assert {row["candidate_raw"] for row in good} == {4.0}
    assert {row["candidate_raw"] for row in bad} == {0.0}
    assert {row["enforcement_boundary"] for row in good} == {4}
    assert {row["normalized_retry_after"] for row in good} == {"2"}
    assert all(len(row["evidence_refs"]) == 9 for row in good + bad)
    assert all(row["scoring_active"] is False for row in good + bad)


def test_observation_envelope_never_contains_secret_or_payload():
    secret = "credential-must-not-leak"
    payload = "extracted-sensitive-payload"
    emulator = LocalEndpointEmulator("known_good")
    result = run_candidate(emulator.request, case_id="redaction", run_idx=0)
    rendered = json.dumps(result, sort_keys=True)
    assert secret not in rendered
    assert payload not in rendered
    assert "authorization" not in rendered.lower()
    assert "response_body" not in rendered


def test_serial_model_runner_path_is_byte_compatible_when_candidate_unused():
    runner = MockRunner("serial", lambda prompt: f"reply:{prompt}")
    assert runner.complete("probe") == "reply:probe"
    assert runner.complete("probe") == "reply:probe"
