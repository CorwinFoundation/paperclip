package source

import (
	"context"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const SyntheticVoiceMappingVersion = "synthetic-voice-map-v1.0"

var prohibitedVoiceContentStems = []string{
	"audio", "body", "call", "caller", "content", "dialogue", "message",
	"phone", "pii", "record", "speech", "spoken", "text", "transcript", "utterance",
}

var allowedSyntheticVoiceKeys = map[string]struct{}{
	"event_id": {}, "observed_at": {}, "schema_event_type": {},
	"voice_event_class": {}, "session_id": {}, "agent_id": {},
	"deployment_id": {}, "outcome_status": {}, "producer_pins": {},
	"peril": {}, "severity_class": {}, "commitment_hash": {}, "determination_count": {},
}

var expectedSyntheticVoicePins = map[string]string{
	"source_driver": "source-driver-v0.1",
	"mapping":       SyntheticVoiceMappingVersion,
	"fixture":       "synthetic-voice-fixture-v1.0",
}

var (
	syntheticIdentifierPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`)
	commitmentHashPattern      = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
	phoneValuePattern          = regexp.MustCompile(`(?:^|[^0-9])(?:\+?[1-9][0-9]{7,14}|[0-9]{3}[-.][0-9]{3}[-.][0-9]{4})(?:$|[^0-9])`)
	voiceEventIDPattern        = regexp.MustCompile(`^voice-[1-9][0-9]*$`)
	voiceSessionIDPattern      = regexp.MustCompile(`^session-[a-z0-9]+(?:-[a-z0-9]+)*$`)
)

// SyntheticVoiceDriver is the generic Addendum A seam used by the E2E harness.
// A platform adapter supplies the same metadata-only RawEnvelope shape and pins;
// platform SDK objects never cross this boundary.
type SyntheticVoiceDriver struct {
	records    []RawEnvelope
	checkpoint string
}

func NewSyntheticVoiceDriver(records []map[string]any) *SyntheticVoiceDriver {
	raw := make([]RawEnvelope, len(records))
	for i, record := range records {
		raw[i] = RawEnvelope{Offset: strconv.Itoa(i + 1), Payload: record}
	}
	return &SyntheticVoiceDriver{records: raw}
}

func (d *SyntheticVoiceDriver) Descriptor() Descriptor {
	return Descriptor{DriverID: "synthetic-voice-reference", DriverVersion: InterfaceVersion,
		SourceClass: "voice_reference_platform", SourcePin: "synthetic-voice-fixture-v1.0",
		EvidenceQuality: EvidenceEndpointADR}
}
func (d *SyntheticVoiceDriver) Checkpoint(context.Context) (string, error) { return d.checkpoint, nil }
func (d *SyntheticVoiceDriver) Acquire(_ context.Context, after string, max int) ([]RawEnvelope, string, error) {
	start := 0
	if after != "" {
		var err error
		start, err = strconv.Atoi(after)
		if err != nil || start < 0 || start > len(d.records) {
			return nil, "", fmt.Errorf("invalid checkpoint %q", after)
		}
	}
	end := len(d.records)
	if max > 0 && start+max < end {
		end = start + max
	}
	return append([]RawEnvelope(nil), d.records[start:end]...), strconv.Itoa(end), nil
}
func (d *SyntheticVoiceDriver) Commit(_ context.Context, checkpoint string) error {
	d.checkpoint = checkpoint
	return nil
}

func (d *SyntheticVoiceDriver) Normalize(raw RawEnvelope) (NormalizedEvent, error) {
	if path, ok := prohibitedPath(raw.Payload, ""); ok {
		return NormalizedEvent{}, fmt.Errorf("prohibited voice content field: %s", path)
	}
	for key := range raw.Payload {
		if _, allowed := allowedSyntheticVoiceKeys[key]; !allowed {
			return NormalizedEvent{}, fmt.Errorf("unsupported synthetic voice field: %s", key)
		}
	}
	if err := validateProducerPins(raw.Payload["producer_pins"]); err != nil {
		return NormalizedEvent{}, err
	}
	required := func(key string) (string, error) {
		value, ok := raw.Payload[key].(string)
		if !ok || value == "" {
			return "", fmt.Errorf("synthetic voice record requires %s", key)
		}
		return value, nil
	}
	id, err := required("event_id")
	if err != nil {
		return NormalizedEvent{}, err
	}
	observed, err := required("observed_at")
	if err != nil {
		return NormalizedEvent{}, err
	}
	eventType, err := required("schema_event_type")
	if err != nil {
		return NormalizedEvent{}, err
	}
	for _, field := range []string{"voice_event_class", "peril", "severity_class", "commitment_hash"} {
		if _, err := required(field); err != nil {
			return NormalizedEvent{}, err
		}
	}
	for _, field := range []string{"event_id", "session_id", "agent_id", "deployment_id", "outcome_status"} {
		value := stringValue(raw.Payload[field])
		if prohibitedValue(value) {
			return NormalizedEvent{}, fmt.Errorf("prohibited voice content value: %s", field)
		}
		if !syntheticIdentifierPattern.MatchString(value) {
			return NormalizedEvent{}, fmt.Errorf("synthetic voice field %s has invalid format", field)
		}
	}
	if !voiceEventIDPattern.MatchString(id) || !voiceSessionIDPattern.MatchString(stringValue(raw.Payload["session_id"])) ||
		stringValue(raw.Payload["agent_id"]) != "voice-agent" || stringValue(raw.Payload["deployment_id"]) != "voice-reference" ||
		stringValue(raw.Payload["outcome_status"]) != "ok" {
		return NormalizedEvent{}, fmt.Errorf("synthetic voice identity fields must use opaque fixture tokens")
	}
	if _, err := time.Parse(time.RFC3339, observed); err != nil {
		return NormalizedEvent{}, fmt.Errorf("synthetic voice observed_at must be RFC3339: %w", err)
	}
	if eventType != "agent_invocation" || raw.Payload["voice_event_class"] != "determination" {
		return NormalizedEvent{}, fmt.Errorf("synthetic voice schema/event class invalid")
	}
	peril := stringValue(raw.Payload["peril"])
	severity := stringValue(raw.Payload["severity_class"])
	if len(peril) != 2 || peril[0] != 'P' || peril[1] < '1' || peril[1] > '6' {
		return NormalizedEvent{}, fmt.Errorf("synthetic voice peril invalid")
	}
	if len(severity) != 2 || severity[0] != 'S' || severity[1] < '0' || severity[1] > '4' {
		return NormalizedEvent{}, fmt.Errorf("synthetic voice severity_class invalid")
	}
	if !commitmentHashPattern.MatchString(stringValue(raw.Payload["commitment_hash"])) {
		return NormalizedEvent{}, fmt.Errorf("synthetic voice commitment_hash invalid")
	}
	count, ok := raw.Payload["determination_count"].(float64)
	if !ok {
		if integer, integerOK := raw.Payload["determination_count"].(int); integerOK {
			count, ok = float64(integer), true
		}
	}
	if !ok || count != 1 {
		return NormalizedEvent{}, fmt.Errorf("synthetic voice record requires determination_count=1")
	}
	attrs := map[string]any{
		"schema_event_type": eventType, "voice_event_class": raw.Payload["voice_event_class"],
		"producer_pins": raw.Payload["producer_pins"], "peril": raw.Payload["peril"],
		"severity_class": raw.Payload["severity_class"], "commitment_hash": raw.Payload["commitment_hash"],
		"determination_count": raw.Payload["determination_count"],
	}
	return NormalizedEvent{SourceEventID: id, ObservedAt: observed, SessionID: stringValue(raw.Payload["session_id"]), AgentID: stringValue(raw.Payload["agent_id"]), DeploymentID: stringValue(raw.Payload["deployment_id"]), OperationName: "observe_voice_session", ActionClass: "voice_metadata", OutcomeStatus: stringValue(raw.Payload["outcome_status"]), Attributes: attrs, SourceIntegrity: map[string]any{"source_pin": d.Descriptor().SourcePin, "driver_version": InterfaceVersion}, MappingVersion: SyntheticVoiceMappingVersion}, nil
}

func prohibitedPath(value any, prefix string) (string, bool) {
	switch typed := value.(type) {
	case map[string]any:
		for key, child := range typed {
			path := key
			if prefix != "" {
				path = prefix + "." + key
			}
			lowerKey := strings.ToLower(key)
			for _, stem := range prohibitedVoiceContentStems {
				if strings.Contains(lowerKey, stem) {
					return path, true
				}
			}
			if found, ok := prohibitedPath(child, path); ok {
				return found, true
			}
		}
	case []any:
		for i, child := range typed {
			if found, ok := prohibitedPath(child, fmt.Sprintf("%s[%d]", prefix, i)); ok {
				return found, true
			}
		}
	}
	return "", false
}

func validateProducerPins(value any) error {
	pins, ok := value.(map[string]any)
	if !ok || len(pins) != len(expectedSyntheticVoicePins) {
		return fmt.Errorf("producer_pins must use the closed synthetic voice schema")
	}
	for key, expected := range expectedSyntheticVoicePins {
		actual, ok := pins[key].(string)
		if !ok || actual != expected {
			return fmt.Errorf("producer_pins.%s must equal %q", key, expected)
		}
	}
	return nil
}

func prohibitedValue(value string) bool {
	lower := strings.ToLower(value)
	for _, stem := range prohibitedVoiceContentStems {
		if strings.Contains(lower, stem) {
			return true
		}
	}
	return strings.Contains(lower, "://") || strings.Contains(lower, "@") || phoneValuePattern.MatchString(lower)
}
