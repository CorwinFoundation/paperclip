from __future__ import annotations

import json

import pytest

from determination.voice_cel import FORBIDDEN_KEYS, VoiceCelError, VoiceCelPack, reject_forbidden_keys


@pytest.fixture(scope="module")
def pack() -> VoiceCelPack:
    return VoiceCelPack.load()


@pytest.mark.parametrize(
    ("intent_id", "violation_field", "baseline_field"),
    [
        ("consequential_action_without_confirmation", "unconfirmed_action_count", "action_count"),
        ("pii_disclosure_metadata_signal", "disclosure_signal_count", "inspected_interaction_count"),
        ("ai_disclosure_missing", "missing_disclosure_count", "interaction_count"),
        ("unauthorized_transfer", "unauthorized_transfer_count", "transfer_count"),
    ],
)
@pytest.mark.parametrize(("left", "right", "expected"), [(0, 1, "pass"), (2, 1, "fail"), (None, 1, "indeterminate"), (0, None, "indeterminate")])
def test_compile_and_evaluate_truth_tables(pack, intent_id, violation_field, baseline_field, left, right, expected):
    evidence = {violation_field: left, baseline_field: right}
    result = pack.evaluate(intent_id, evidence, commitment_ref="sha256:0000", source_state="healthy")
    assert result["verdict"] == expected
    assert result["rule_pack_version"] == "voice-perils-cel@1.0.0"
    assert "expression" not in result


@pytest.mark.parametrize("forbidden", sorted(FORBIDDEN_KEYS))
@pytest.mark.parametrize("depth", [0, 1, 2, 3])
def test_recursive_forbidden_key_negative_corpus(forbidden, depth):
    value = {forbidden: "fixture-secret"}
    for index in range(depth):
        value = {f"level_{index}": [value]}
    with pytest.raises(VoiceCelError, match="forbidden key"):
        reject_forbidden_keys(value)


def test_key_normalisation_catches_hyphenated_spoken_pii():
    with pytest.raises(VoiceCelError, match="forbidden key"):
        reject_forbidden_keys({"outer": [{"spoken-PII": True}]})


def test_output_is_closed_content_free_and_version_pinned(pack):
    result = pack.evaluate(
        "unauthorized_transfer",
        {"unauthorized_transfer_count": 0, "transfer_count": 4},
        commitment_ref="sha256:abcd",
        source_state="healthy",
    )
    assert set(result) == {"rule_pack_version", "intent_id", "verdict", "observed_count", "commitment_ref", "health"}
    serialised = json.dumps(result, sort_keys=True)
    assert "fixture-secret" not in serialised
    reject_forbidden_keys(result)


def test_unknown_evidence_field_fails_closed(pack):
    with pytest.raises(VoiceCelError, match="outside the rule allowlist"):
        pack.evaluate(
            "unauthorized_transfer",
            {"unauthorized_transfer_count": 0, "transfer_count": 1, "vendor_blob": {}},
            commitment_ref="sha256:abcd",
            source_state="healthy",
        )
