from __future__ import annotations

import json

import pytest

from determination.voice_cel import FORBIDDEN_KEYS, SOURCE_STATES, VoiceCelError, VoiceCelPack, reject_forbidden_keys


COMMITMENT_REF = "sha256:" + "0" * 64


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
    result = pack.evaluate(intent_id, evidence, commitment_ref=COMMITMENT_REF, source_state="healthy")
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
        commitment_ref=COMMITMENT_REF,
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
            commitment_ref=COMMITMENT_REF,
            source_state="healthy",
        )


@pytest.mark.parametrize(
    "commitment_ref",
    [
        "",
        None,
        "sha256:",
        "sha256:abcd",
        "sha256:" + "g" * 64,
        "SHA256:" + "0" * 64,
        "tenant-b:sha256:" + "0" * 64,
        "sha256:" + "0" * 64 + "\nRAW TRANSCRIPT: customer SSN 123-45-6789",
    ],
)
def test_commitment_ref_value_validation_fails_closed(pack, commitment_ref):
    with pytest.raises(VoiceCelError, match="commitment_ref"):
        pack.evaluate(
            "unauthorized_transfer",
            {"unauthorized_transfer_count": 0, "transfer_count": 1},
            commitment_ref=commitment_ref,
            source_state="healthy",
        )


@pytest.mark.parametrize("source_state", ["", None, "Healthy", "unknown", "tenant-b:healthy", "healthy\ntranscript=secret"])
def test_source_state_value_validation_fails_closed(pack, source_state):
    with pytest.raises(VoiceCelError, match="source_state"):
        pack.evaluate(
            "unauthorized_transfer",
            {"unauthorized_transfer_count": 0, "transfer_count": 1},
            commitment_ref=COMMITMENT_REF,
            source_state=source_state,
        )


@pytest.mark.parametrize("source_state", sorted(SOURCE_STATES))
@pytest.mark.parametrize("intent_id", [rule.intent_id for rule in VoiceCelPack.load().rules])
def test_adjacent_voice_rules_accept_only_canonical_output_metadata(pack, intent_id, source_state):
    rule = next(rule for rule in pack.rules if rule.intent_id == intent_id)
    result = pack.evaluate(
        intent_id,
        {rule.violation_field: 0, rule.baseline_field: 1},
        commitment_ref=COMMITMENT_REF,
        source_state=source_state,
    )
    assert result["commitment_ref"] == COMMITMENT_REF
    assert result["health"] == {"source_state": source_state}
