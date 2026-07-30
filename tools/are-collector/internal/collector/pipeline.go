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
	"backbond/are-collector/internal/source"
)

type Pipeline struct {
	Adapter adapter.LogSinkAdapter
	Policy  redaction.Policy
}

// CollectDriver crosses the versioned SourceDriver boundary. Checkpoints advance
// only after every acquired record has normalized, redacted, and validated.
func (pipeline Pipeline) CollectDriver(ctx context.Context, driver source.SourceDriver, maxEvents int) ([]schemav1.Event, error) {
	if driver == nil {
		return nil, fmt.Errorf("source driver is required")
	}
	checkpoint, err := driver.Checkpoint(ctx)
	if err != nil {
		return nil, err
	}
	records, next, err := driver.Acquire(ctx, checkpoint, maxEvents)
	if err != nil {
		return nil, err
	}
	events := make([]schemav1.Event, 0, len(records))
	for _, record := range records {
		normalized, err := driver.Normalize(record)
		if err != nil {
			return nil, err
		}
		eventType := schemav1.EventType(normalized.Attributes["schema_event_type"].(string))
		payload := map[string]any{
			"operation_name":   normalized.OperationName,
			"action_class":     normalized.ActionClass,
			"billable":         normalized.Billable,
			"outcome_status":   normalized.OutcomeStatus,
			"session_id":       normalized.SessionID,
			"agent_id":         normalized.AgentID,
			"deployment_id":    normalized.DeploymentID,
			"mapping_version":  normalized.MappingVersion,
			"source_integrity": normalized.SourceIntegrity,
			"attributes":       normalized.Attributes,
		}
		event := schemav1.NewEvent(normalized.SourceEventID, eventType, normalized.ObservedAt, schemav1.SourceNumbat, payload)
		policy := redaction.Policy{}
		for _, path := range normalized.SensitivePaths {
			policy["attributes."+path] = redaction.Drop
		}
		event, err = redaction.Apply(event, policy)
		if err != nil {
			return nil, err
		}
		if err := schemav1.ValidateEvent(event); err != nil {
			return nil, err
		}
		events = append(events, event)
	}
	if err := driver.Commit(ctx, next); err != nil {
		return nil, err
	}
	return events, nil
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
