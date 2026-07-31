import json
from pathlib import Path

import pytest

from harness.vendor_voice_reference import (
    NormalizedVoiceEvent,
    VoiceReferenceError,
    determine_voice_payload,
    validate_output,
)


ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "harness/fixtures/synthetic_voice_events_v1.json"


def _fixture():
    return json.loads(FIXTURE.read_text())


def test_synthetic_harness_emits_only_closed_content_free_schema():
    result = determine_voice_payload(_fixture())
    assert result == {
        "determination": "review",
        "action_class_counts": {"customer_support": 1, "data_access": 1},
        "evidence_commitment_hash": result["evidence_commitment_hash"],
    }
    assert result["evidence_commitment_hash"].startswith("sha256:")
    serialized = json.dumps(result).lower()
    for prohibited in ("audio", "recording", "transcript", "spoken_pii", "excerpt", "payload"):
        assert prohibited not in serialized


def test_commitment_is_stable_and_content_sensitive_without_disclosing_content():
    first = determine_voice_payload(_fixture())
    second = determine_voice_payload(_fixture())
    changed = _fixture()
    changed["events"][0]["transcript"] += " Synthetic-only change."
    third = determine_voice_payload(changed)
    assert first == second
    assert first["evidence_commitment_hash"] != third["evidence_commitment_hash"]
    assert first["action_class_counts"] == third["action_class_counts"]


@pytest.mark.parametrize("payload", [
    {},
    {"schema": "synthetic-voice-events/v1", "events": []},
    {"schema": "synthetic-voice-events/v1", "events": [{"transcript": "secret"}]},
])
def test_malformed_input_fails_closed_without_echoing_content(payload):
    with pytest.raises(VoiceReferenceError) as raised:
        determine_voice_payload(payload)
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize("field", ["audio", "recording", "transcript", "spoken_pii", "excerpt", "call_payload"])
def test_prohibited_output_attempts_are_rejected(field):
    candidate = determine_voice_payload(_fixture())
    candidate[field] = "must not cross boundary"
    with pytest.raises(VoiceReferenceError, match="closed determination schema"):
        validate_output(candidate)


def test_thin_adapter_can_translate_future_event_names_without_platform_io():
    class FutureNamingAdapter:
        def normalize(self, payload):
            assert payload == {"future": "signature"}
            return [NormalizedVoiceEvent(1, "future.completed", "message_send", "blocked", "synthetic")]

    result = determine_voice_payload({"future": "signature"}, adapter=FutureNamingAdapter())
    assert result["determination"] == "deny"
    assert result["action_class_counts"] == {"message_send": 1}
