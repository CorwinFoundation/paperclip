package source

import (
	"strings"
	"testing"
)

// Control: validSyntheticVoiceRecord() must normalize cleanly. Without this
// test the fixture can drift into a shape that Normalize rejects for an
// unrelated reason (before this candidate, event_id "v1" and session_id "s1"
// failed voiceEventIDPattern/voiceSessionIDPattern), and every rejection test
// below then passes for the wrong reason. Mutate any field of the fixture to
// an invalid value and this test fails.
func TestSyntheticVoiceAcceptsValidRecord(t *testing.T) {
	d := NewSyntheticVoiceDriver(nil)
	event, err := d.Normalize(RawEnvelope{Payload: validSyntheticVoiceRecord()})
	if err != nil {
		t.Fatalf("valid synthetic voice record rejected: %v", err)
	}
	if event.SourceEventID != "voice-1" || event.SessionID != "session-s1" {
		t.Fatalf("normalized identity fields not carried: %+v", event)
	}
	if event.MappingVersion != SyntheticVoiceMappingVersion {
		t.Fatalf("mapping version = %q, want %q", event.MappingVersion, SyntheticVoiceMappingVersion)
	}
}

// Control: replace the prohibitedPath call at synthetic_voice.go:83 with a
// no-op and this test fails on the first key, because the surrounding record
// is otherwise valid (TestSyntheticVoiceAcceptsValidRecord) and the asserted
// error substring is produced only by that guard.
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
		_, err := d.Normalize(RawEnvelope{Payload: row})
		if err == nil {
			t.Fatalf("accepted prohibited %s", key)
		}
		if want := "prohibited voice content field: producer_pins." + key; !strings.Contains(err.Error(), want) {
			t.Fatalf("prohibited %s rejected for non-content reason: %v", key, err)
		}
	}
}

// Control: replace the unknown-key guard at synthetic_voice.go:86-90 with a
// continue and this test fails, because the rest of the record is valid and
// the asserted error substring is produced only by that guard.
func TestSyntheticVoiceRejectsUnknownFields(t *testing.T) {
	d := NewSyntheticVoiceDriver(nil)
	row := validSyntheticVoiceRecord()
	row["opaque_payload"] = "must fail closed"
	_, err := d.Normalize(RawEnvelope{Payload: row})
	if err == nil {
		t.Fatal("accepted field outside metadata-only schema")
	}
	if want := "unsupported synthetic voice field: opaque_payload"; !strings.Contains(err.Error(), want) {
		t.Fatalf("unknown field rejected for non-schema reason: %v", err)
	}
}

// Control: make validateProducerPins return nil unconditionally and this test
// fails. The injected pin value carries no prohibited key, so prohibitedPath
// does not see it and only the closed value schema rejects it.
func TestSyntheticVoiceRejectsProducerPinValueInjection(t *testing.T) {
	d := NewSyntheticVoiceDriver(nil)
	row := validSyntheticVoiceRecord()
	row["producer_pins"].(map[string]any)["fixture"] = "synthetic-voice-fixture-v1.0 https://example.invalid/transcript"
	_, err := d.Normalize(RawEnvelope{Payload: row})
	if err == nil {
		t.Fatal("accepted producer pin outside closed value schema")
	}
	if want := `producer_pins.fixture must equal "synthetic-voice-fixture-v1.0"`; !strings.Contains(err.Error(), want) {
		t.Fatalf("pin injection rejected for non-pin reason: %v", err)
	}
}

// Control: make prohibitedValue return false and this test fails on the first
// value, because the asserted error substring is produced only by the value
// scan at synthetic_voice.go:118-122.
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

// Control: drop the []any case from prohibitedPath, or stop recursing into
// child values, and this test fails. It is the only case that drives a
// prohibited key nested under an allowed key through a slice.
func TestProhibitedPathMatchesNestedEquivalentUnderAllowedKey(t *testing.T) {
	value := map[string]any{"producer_pins": map[string]any{"nested": []any{map[string]any{"transcript_url": "forbidden"}}}}
	if path, ok := prohibitedPath(value, ""); !ok || path != "producer_pins.nested[0].transcript_url" {
		t.Fatalf("recursive prohibited path not measured: path=%q ok=%v", path, ok)
	}
}

// validSyntheticVoiceRecord returns a record that Normalize accepts. Every
// rejection test above mutates exactly one aspect of it, so the fixture must
// stay valid or those tests stop measuring their guard.
func validSyntheticVoiceRecord() map[string]any {
	return map[string]any{
		"event_id": "voice-1", "observed_at": "2026-07-31T00:00:00Z", "schema_event_type": "agent_invocation",
		"voice_event_class": "determination", "session_id": "session-s1", "agent_id": "voice-agent",
		"deployment_id": "voice-reference", "outcome_status": "ok", "peril": "P2",
		"severity_class": "S1", "commitment_hash": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef", "determination_count": 1,
		"producer_pins": map[string]any{"source_driver": "source-driver-v0.1", "mapping": "synthetic-voice-map-v1.0", "fixture": "synthetic-voice-fixture-v1.0"},
	}
}
