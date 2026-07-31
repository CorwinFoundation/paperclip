"""BEAAA-20003 vendor-side deterministic voice determination acceptance."""

from __future__ import annotations

import json

import pytest

from determination.vendor_voice import (
    VoiceDeterminationError,
    VoiceMetadataEvent,
    assert_serialization_boundary,
    determine_voice_metadata,
)


SECRET = b"synthetic-commitment-key"
FIXTURE_SECRETS = ("spoken-secret-7391", "synthetic@example.invalid", "+15555550123")


def event(event_id="evt-a", sequence=1, **changes):
    values = {
        "consequential_action_completed": True,
        "confirmation_valid": True,
        "pii_disclosure_detected": False,
        "ai_disclosure_present": True,
        "transfer_completed": True,
        "transfer_authorized": True,
    }
    values.update(changes)
    return VoiceMetadataEvent(event_id=event_id, sequence=sequence, **values)


def by_peril(result):
    return {row["peril"]: row for row in result["determinations"]}


@pytest.mark.parametrize(
    ("peril", "field", "passing", "failing"),
    [
        ("consequential_action_without_confirmation", "confirmation_valid", True, False),
        ("pii_disclosure", "pii_disclosure_detected", False, True),
        ("ai_disclosure_missing", "ai_disclosure_present", True, False),
        ("unauthorized_transfer", "transfer_authorized", True, False),
    ],
)
def test_four_rule_truth_tables_pass_fail_and_missing(peril, field, passing, failing):
    passed = by_peril(determine_voice_metadata([event(**{field: passing})], commitment_secret=SECRET))
    failed = by_peril(determine_voice_metadata([event(**{field: failing})], commitment_secret=SECRET))
    missing = by_peril(determine_voice_metadata([event(**{field: None})], commitment_secret=SECRET))
    assert passed[peril]["verdict"] == "pass"
    assert failed[peril]["verdict"] == "fail"
    assert missing[peril]["verdict"] == "indeterminate"


def test_empty_or_impaired_telemetry_is_indeterminate_never_pass():
    empty = determine_voice_metadata([], commitment_secret=SECRET)
    impaired = determine_voice_metadata([event()], commitment_secret=SECRET, source_health="unreachable")
    assert {row["verdict"] for row in empty["determinations"]} == {"indeterminate"}
    assert {row["verdict"] for row in impaired["determinations"]} == {"indeterminate"}
    assert not empty["health"]["telemetry_complete"]
    assert not impaired["health"]["telemetry_complete"]


def test_duplicates_are_idempotent_and_out_of_order_is_deterministic():
    first = event("evt-a", 1)
    second = event("evt-b", 2, confirmation_valid=False)
    ordered = determine_voice_metadata([first, second], commitment_secret=SECRET)
    duplicate = determine_voice_metadata([first, first, second], commitment_secret=SECRET)
    reversed_input = determine_voice_metadata([second, first], commitment_secret=SECRET)
    assert duplicate == ordered == reversed_input


def test_conflicting_duplicate_fails_closed():
    with pytest.raises(VoiceDeterminationError, match="conflicting_duplicate_event"):
        determine_voice_metadata(
            [event("evt-a", 1), event("evt-a", 1, confirmation_valid=False)],
            commitment_secret=SECRET,
        )


def test_output_stdout_stderr_and_logs_contain_no_fixture_content(capsys, caplog):
    # Secrets intentionally exist beside the synthetic metadata fixture but are
    # never accepted by the component's closed input type.
    fixture = {"metadata": event(), "local_content": FIXTURE_SECRETS}
    result = determine_voice_metadata([fixture["metadata"]], commitment_secret=SECRET)
    serialized = json.dumps(result, sort_keys=True)
    captured = capsys.readouterr()
    emitted = "\n".join([serialized, captured.out, captured.err, caplog.text])
    for secret in FIXTURE_SECRETS:
        assert secret not in emitted


def test_serialization_boundary_rejects_extra_content_at_every_public_level():
    result = determine_voice_metadata([event()], commitment_secret=SECRET)
    result["transcript"] = "forbidden"
    with pytest.raises(VoiceDeterminationError, match="unsafe_output_shape"):
        assert_serialization_boundary(result)

    result = determine_voice_metadata([event()], commitment_secret=SECRET)
    result["determinations"][0]["content"] = "forbidden"
    with pytest.raises(VoiceDeterminationError, match="unsafe_determination_shape"):
        assert_serialization_boundary(result)

    result = determine_voice_metadata([event()], commitment_secret=SECRET)
    result["health"]["raw_event"] = "forbidden"
    with pytest.raises(VoiceDeterminationError, match="unsafe_health_shape"):
        assert_serialization_boundary(result)


def test_output_is_byte_stable_across_n5_runs():
    events = [event("evt-b", 2), event("evt-a", 1)]
    payloads = [
        json.dumps(determine_voice_metadata(events, commitment_secret=SECRET), sort_keys=True)
        for _ in range(5)
    ]
    assert len(set(payloads)) == 1
