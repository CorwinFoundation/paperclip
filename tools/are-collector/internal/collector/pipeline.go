package collector

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"time"

	"backbond/are-collector/internal/adapter"
	"backbond/are-collector/internal/redaction"
	"backbond/are-collector/internal/schemav1"
)

type Pipeline struct {
	Adapter adapter.LogSinkAdapter
	Policy  redaction.Policy
}

func (pipeline Pipeline) Collect(ctx context.Context) ([]schemav1.Event, error) {
	if pipeline.Adapter == nil {
		return nil, fmt.Errorf("adapter is required")
	}
	stream, err := pipeline.Adapter.Stream(ctx)
	if err != nil {
		return nil, err
	}
	events := []schemav1.Event{}
	for record := range stream {
		projected, ok := Project(record)
		if !ok {
			continue
		}
		redacted, err := redaction.Apply(projected, pipeline.Policy)
		if err != nil {
			return nil, err
		}
		if err := schemav1.ValidateEvent(redacted); err != nil {
			return nil, err
		}
		events = append(events, redacted)
	}
	return events, nil
}

func Project(record adapter.RawRecord) (schemav1.Event, bool) {
	payload := record.VendorPayload
	eventTypeRaw, ok := firstString(payload, "event_type", "eventType", "type", "family")
	if !ok {
		return schemav1.Event{}, false
	}
	eventType := schemav1.EventType(eventTypeRaw)
	eventID, ok := firstString(payload, "event_id", "eventId", "id")
	if !ok || eventID == "" {
		eventID = derivedEventID(record)
	}
	loggedAt, ok := firstString(payload, "logged_at", "loggedAt", "timestamp", "time")
	if !ok || loggedAt == "" {
		loggedAt = time.Unix(0, 0).UTC().Format(time.RFC3339)
	}
	eventPayload := map[string]any{}
	if nested, ok := payload["payload"].(map[string]any); ok {
		eventPayload = nested
	} else {
		for key, value := range payload {
			if key == "event_type" || key == "eventType" || key == "type" || key == "family" ||
				key == "event_id" || key == "eventId" || key == "id" ||
				key == "logged_at" || key == "loggedAt" || key == "timestamp" || key == "time" {
				continue
			}
			eventPayload[key] = value
		}
	}
	event := schemav1.NewEvent(eventID, eventType, loggedAt, record.SourceKind, eventPayload)
	if err := schemav1.ValidateEvent(event); err != nil {
		return schemav1.Event{}, false
	}
	return event, true
}

func firstString(payload map[string]any, keys ...string) (string, bool) {
	for _, key := range keys {
		value, ok := payload[key]
		if !ok {
			continue
		}
		if text, ok := value.(string); ok {
			return text, true
		}
	}
	return "", false
}

func derivedEventID(record adapter.RawRecord) string {
	canonical, err := schemav1.CanonicalJSON(record.VendorPayload)
	if err != nil {
		return "evt-invalid"
	}
	digest := sha256.Sum256(canonical)
	return "evt-" + hex.EncodeToString(digest[:8])
}
