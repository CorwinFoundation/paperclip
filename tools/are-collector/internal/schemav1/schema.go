package schemav1

import (
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"time"
)

const Version = "log-event-schema-v1.0"

type SourceKind string

const (
	SourceDatadog    SourceKind = "datadog"
	SourceSplunk     SourceKind = "splunk"
	SourceCloudWatch SourceKind = "cloudwatch"
	SourceOTEL       SourceKind = "otel"
	SourceNumbat     SourceKind = "numbat"
)

type EventType string

const (
	EventAgentInvocation EventType = "agent_invocation"
	EventLLMCompletion   EventType = "llm_completion"
	EventToolCall        EventType = "tool_call"
	EventToolSourceRef   EventType = "tool_source_reference"
	EventEscalation      EventType = "escalation"
	EventError           EventType = "error"
	EventSecurity        EventType = "security_event"
	EventPIIEmission     EventType = "pii_emission"
	EventClaimCandidate  EventType = "claim_candidate_event"
)

var allowedEventTypes = map[EventType]struct{}{
	EventAgentInvocation: {},
	EventLLMCompletion:   {},
	EventToolCall:        {},
	EventToolSourceRef:   {},
	EventEscalation:      {},
	EventError:           {},
	EventSecurity:        {},
	EventPIIEmission:     {},
	EventClaimCandidate:  {},
}

type Event struct {
	EventID          string         `json:"event_id"`
	EventType        EventType      `json:"event_type"`
	LoggedAt         string         `json:"logged_at"`
	SchemaVersion    string         `json:"schema_version"`
	SourceKind       SourceKind     `json:"source_kind"`
	Payload          map[string]any `json:"payload"`
	RedactionApplied []string       `json:"redaction_applied"`
}

func NewEvent(eventID string, eventType EventType, loggedAt string, source SourceKind, payload map[string]any) Event {
	return Event{
		EventID:          eventID,
		EventType:        eventType,
		LoggedAt:         loggedAt,
		SchemaVersion:    Version,
		SourceKind:       source,
		Payload:          payload,
		RedactionApplied: []string{},
	}
}

func ValidateEvent(event Event) error {
	if event.SchemaVersion != Version {
		return fmt.Errorf("schema_version must be %q", Version)
	}
	if event.EventID == "" {
		return errors.New("event_id is required")
	}
	if _, ok := allowedEventTypes[event.EventType]; !ok {
		return fmt.Errorf("unsupported event_type: %q", event.EventType)
	}
	if event.LoggedAt == "" {
		return errors.New("logged_at is required")
	}
	if _, err := time.Parse(time.RFC3339, event.LoggedAt); err != nil {
		return fmt.Errorf("logged_at must be RFC3339: %w", err)
	}
	if event.Payload == nil {
		return errors.New("payload is required")
	}
	if event.RedactionApplied == nil {
		return errors.New("redaction_applied is required")
	}
	sort.Strings(event.RedactionApplied)
	return nil
}

func CanonicalJSON(value any) ([]byte, error) {
	return json.Marshal(canonicalize(value))
}

func canonicalize(value any) any {
	switch typed := value.(type) {
	case map[string]any:
		keys := make([]string, 0, len(typed))
		for key := range typed {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		out := make(map[string]any, len(typed))
		for _, key := range keys {
			out[key] = canonicalize(typed[key])
		}
		return out
	case []any:
		out := make([]any, len(typed))
		for idx, item := range typed {
			out[idx] = canonicalize(item)
		}
		return out
	default:
		return typed
	}
}
