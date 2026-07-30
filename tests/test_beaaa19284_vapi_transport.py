import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "build_beaaa19284_vapi_transport.py"
SPEC = importlib.util.spec_from_file_location("vapi_transport", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_exact_transport_reconciles_all_502_rows():
    summary, records = MODULE.build(MODULE.DEFAULT_SOURCE)
    assert summary["counts"] == {
        "structurally_constrained": 502,
        "original_structural": 477,
        "privacy_minimization_reidentification_role_bearing": 25,
        "executable_exact_transport": 290,
        "genuine_impossibility": 212,
        "vapi_requests": 0,
    }
    assert len({record["stable_case_id"] for record in records}) == 502


def test_every_executable_step_is_byte_identical_and_resume_unique():
    _, records = MODULE.build(MODULE.DEFAULT_SOURCE)
    tokens = []
    for record in records:
        manifest = record["transport_manifest"]
        if manifest is None:
            assert record["missing_primitive"]
            assert record["remediation_owner"]
            continue
        for branch in manifest["branches"]:
            for step in branch["steps"]:
                assert MODULE.sha256_bytes(step["content"].encode("utf-8")) == step["content_utf8_sha256"]
                tokens.append((record["stable_case_id"], step["resume_token"]))
    assert len(tokens) == len(set(tokens))


def test_role_bearing_cases_are_separate_and_fail_closed():
    _, records = MODULE.build(MODULE.DEFAULT_SOURCE)
    role_bearing = [
        record for record in records
        if record["classification_group"] == "privacy_minimization_reidentification_role_bearing"
    ]
    assert len(role_bearing) == 25
    assert {record["capability"] for record in role_bearing} == {"genuine_impossibility"}
    assert {
        record["missing_primitive"] for record in role_bearing
    } == {"vapi_chat_input_role_for_assistant_observation_and_system_event"}
