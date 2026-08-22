package source

import (
	"strings"
	"testing"
)

func TestSyntheticVoiceRejectsContentRecursively(t *testing.T) {
	d := NewSyntheticVoiceDriver(nil)
	base := validSyntheticVoiceRecord()
	for _, key := range []string{"transcript", "Transcript", "transcript_url", "audio", "recording_uri", "spoken_pii", "speech_text", "caller_phone_number", "utterance", "message_body"} {
		row := map[string]any{}
		for k, v := range base {
			row[k] = v
		}
		pins := map[string]any{}
		for k, v := range base["producer_pins"].(map[string]any) {
			pins[k] = v
		}
		pins[key] = "forbidden"
		row["producer_pins"] = pins
		if _, err := d.Normalize(RawEnvelope{Payload: row}); err == nil {
			t.Fatalf("accepted prohibited %s", key)
		}
	}
}

func TestSyntheticVoiceRejectsUnknownFields(t *testing.T) {
	d := NewSyntheticVoiceDriver(nil)
	row := validSyntheticVoiceRecord()
	row["opaque_payload"] = "must fail closed"
	if _, err := d.Normalize(RawEnvelope{Payload: row}); err == nil {
		t.Fatal("accepted field outside metadata-only schema")
	}
}

func TestSyntheticVoiceRejectsProducerPinValueInjection(t *testing.T) {
	d := NewSyntheticVoiceDriver(nil)
	row := validSyntheticVoiceRecord()
	row["producer_pins"].(map[string]any)["fixture"] = "synthetic-voice-fixture-v1.0 https://example.invalid/transcript"
	if _, err := d.Normalize(RawEnvelope{Payload: row}); err == nil {
		t.Fatal("accepted producer pin outside closed value schema")
	}
}

func TestSyntheticVoiceRejectsContentInjectedIntoAllowedScalar(t *testing.T) {
	d := NewSyntheticVoiceDriver(nil)
	for _, value := range []string{"555-010-9999", "+15550109999", "cdn.example.com:transcript.abc123", "caller.jane.doe-at-example.com"} {
		row := validSyntheticVoiceRecord()
		row["session_id"] = value
		if _, err := d.Normalize(RawEnvelope{Payload: row}); err == nil {
			t.Fatalf("accepted content in closed scalar field: %q", value)
		} else if !strings.Contains(err.Error(), "prohibited voice content value: session_id") {
			t.Fatalf("content case %q rejected for non-content reason: %v", value, err)
		}
	}
}

func TestProhibitedPathMatchesNestedEquivalentUnderAllowedKey(t *testing.T) {
	value := map[string]any{"producer_pins": map[string]any{"nested": []any{map[string]any{"transcript_url": "forbidden"}}}}
	if path, ok := prohibitedPath(value, ""); !ok || path != "producer_pins.nested[0].transcript_url" {
		t.Fatalf("recursive prohibited path not measured: path=%q ok=%v", path, ok)
	}
}

func validSyntheticVoiceRecord() map[string]any {
	return map[string]any{
		"event_id": "v1", "observed_at": "2026-07-31T00:00:00Z", "schema_event_type": "agent_invocation",
		"voice_event_class": "determination", "session_id": "s1", "agent_id": "voice-agent",
		"deployment_id": "voice-reference", "outcome_status": "ok", "peril": "P2",
		"severity_class": "S1", "commitment_hash": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef", "determination_count": 1,
		"producer_pins": map[string]any{"source_driver": "source-driver-v0.1", "mapping": "synthetic-voice-map-v1.0", "fixture": "synthetic-voice-fixture-v1.0"},
	}
}
