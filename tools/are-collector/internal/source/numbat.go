package source

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"strconv"
	"strings"
)

const (
	NumbatCommit         = "83f8c74f"
	NumbatBinarySHA256   = "2e95ac4909db80bfb74aa77f6bed8104173d411a1614bd831e8e65484ef4ecde"
	NumbatMappingVersion = "numbat-map-v0.2"
	NumbatVersion        = "0.1.1"
	NumbatSchemaVersion  = "0.2.0"
)

type NumbatSourceDriver struct {
	records     []RawEnvelope
	checkpoint  string
	recordsRead int
}

func NewNumbatSourceDriver(reader io.Reader) (*NumbatSourceDriver, error) {
	driver := &NumbatSourceDriver{}
	scanner := bufio.NewScanner(reader)
	for scanner.Scan() {
		if strings.TrimSpace(scanner.Text()) == "" {
			continue
		}
		// Counted here, at the read boundary, before the line is parsed, pin
		// checked, or filtered. Any later step that discards the record leaves
		// recordsRead above the emitted event count instead of moving with it.
		driver.recordsRead++
		var payload map[string]any
		if err := json.Unmarshal(scanner.Bytes(), &payload); err != nil {
			return nil, fmt.Errorf("numbat ndjson: %w", err)
		}
		if schema := stringValue(payload["schema_version"]); schema != NumbatSchemaVersion {
			return nil, fmt.Errorf("numbat schema pin mismatch: got %q, require %q", schema, NumbatSchemaVersion)
		}
		driver.records = append(driver.records, RawEnvelope{
			Offset: strconv.Itoa(len(driver.records) + 1), Payload: payload,
		})
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	return driver, nil
}

func (d *NumbatSourceDriver) Descriptor() Descriptor {
	return Descriptor{
		DriverID: "numbat", DriverVersion: InterfaceVersion,
		SourceClass: "coding_agent_endpoint", SourcePin: NumbatCommit,
		EvidenceQuality: EvidenceEndpointADR,
	}
}

func (d *NumbatSourceDriver) Checkpoint(context.Context) (string, error) { return d.checkpoint, nil }

// SourceRecordCount reports records taken off the spool, and is deliberately
// independent of the events the pipeline emits. It is the receipt's
// source_count: derived from len(events) instead, the
// source == emitted + quarantine guard is tautological, because a dropped
// record decrements both sides of the equation equally.
//
// The count spans the whole spool, which is the whole batch while the collector
// acquires without a limit, as the only wired mode does. If a batch limit is
// ever added, an unread remainder leaves source above emitted and the receipt
// is rejected rather than written with an understated source count; that is the
// direction this is meant to fail in.
func (d *NumbatSourceDriver) SourceRecordCount() int { return d.recordsRead }

func (d *NumbatSourceDriver) Acquire(_ context.Context, after string, max int) ([]RawEnvelope, string, error) {
	start := 0
	if after != "" {
		value, err := strconv.Atoi(after)
		if err != nil || value < 0 || value > len(d.records) {
			return nil, "", fmt.Errorf("invalid numbat checkpoint %q", after)
		}
		start = value
	}
	end := len(d.records)
	if max > 0 && start+max < end {
		end = start + max
	}
	out := append([]RawEnvelope(nil), d.records[start:end]...)
	return out, strconv.Itoa(end), nil
}

func (d *NumbatSourceDriver) Commit(_ context.Context, checkpoint string) error {
	value, err := strconv.Atoi(checkpoint)
	if err != nil || value < 0 || value > len(d.records) {
		return fmt.Errorf("invalid numbat checkpoint %q", checkpoint)
	}
	d.checkpoint = checkpoint
	return nil
}

type mapping struct {
	operation string
	action    string
	billable  bool
	eventType string
}

var numbatMappings = map[string]mapping{
	"session.start":        {"invoke_agent", "message", false, "agent_invocation"},
	"session.end":          {"invoke_agent", "message", false, "agent_invocation"},
	"prompt.user":          {"chat", "message", false, "llm_completion"},
	"message.assistant":    {"chat", "message", false, "llm_completion"},
	"message.reasoning":    {"chat", "message", false, "llm_completion"},
	"tool.call":            {"execute_tool", "tool_call", true, "tool_call"},
	"tool.result":          {"execute_tool", "tool_call", false, "tool_call"},
	"command.exec":         {"execute_tool", "tool_call", true, "tool_call"},
	"command.result":       {"execute_tool", "tool_call", false, "tool_call"},
	"file.read":            {"execute_tool", "data_access", true, "tool_source_reference"},
	"file.write":           {"execute_tool", "transaction", true, "tool_call"},
	"file.delete":          {"execute_tool", "transaction", true, "tool_call"},
	"permission.requested": {"execute_tool", "human_escalation", false, "escalation"},
	"permission.approved":  {"execute_tool", "human_escalation", false, "escalation"},
	"permission.denied":    {"execute_tool", "human_escalation", false, "escalation"},
	"config.agent":         {"invoke_agent", "transaction", false, "security_event"},
	"config.mcp":           {"invoke_agent", "transaction", false, "security_event"},
	"network.indicator":    {"execute_tool", "data_access", false, "security_event"},
}

func (d *NumbatSourceDriver) Normalize(raw RawEnvelope) (NormalizedEvent, error) {
	recordType := stringValue(raw.Payload["record_type"])
	kind := stringValue(raw.Payload["event_type"])
	observed := stringValue(raw.Payload["observed_at"])
	id := stringValue(raw.Payload["event_id"])
	if recordType != "" {
		kind, observed, id = officialNumbatIdentity(raw.Payload, recordType)
	} else if kind == "" || observed == "" {
		return NormalizedEvent{}, fmt.Errorf("numbat record requires record_type or legacy event_type and observed_at")
	}
	if id == "" && recordType == "" {
		canonical, _ := json.Marshal(raw.Payload)
		sum := sha256.Sum256(canonical)
		id = "numbat-" + hex.EncodeToString(sum[:8])
	}
	if id == "" {
		return NormalizedEvent{}, fmt.Errorf("numbat %s record requires usable identity", recordType)
	}
	if observed == "" {
		return NormalizedEvent{}, fmt.Errorf("numbat %s record requires usable event time", recordType)
	}
	item, known := numbatMappings[kind]
	knownRecord := recordType == "event" || recordType == "finding" || recordType == "enforcement" || recordType == "indicator" || recordType == "scan_summary" || recordType == "diagnostic"
	if !known {
		// Declared behaviour, not an oversight (BEAAA-23695 QA observation, 2026-08-19):
		// an unrecognised event_type is mapped to agent_invocation and emitted rather
		// than dropped or quarantined, because a monitor-only listener that silently
		// discards inputs it does not recognise is the failure this pilot exists to
		// avoid. The reclassification is not lossy: attributes below carry
		// numbat.event_type with the original string verbatim and numbat.known_kind
		// false, so a consumer can filter unknowns without parsing anything else.
		// What this does NOT do is surface unknown kinds as their own count in the
		// reconciliation receipt. That is a receipt-schema change and belongs in its
		// own increment alongside BEAAA-24444, not here.
		item = mapping{"invoke_agent", "message", false, "agent_invocation"}
	}
	attributes := map[string]any{
		"numbat.event_type":        kind,
		"numbat.known_kind":        known,
		"numbat.record_type":       recordType,
		"numbat.known_record_type": knownRecord,
		"schema_event_type":        item.eventType,
	}
	if recordType == "enforcement" {
		attributes["numbat.enforcement_observed_only"] = true
	}
	if recordType == "indicator" || recordType == "scan_summary" {
		attributes["numbat.synthetic_event_time"] = true
	}
	for _, key := range []string{"call_id", "parent_call_id", "outcome_status", "session_id", "agent_id", "deployment_id", "run_id", "schema_version", "source_agent", "source_type", "decision", "mode", "reason", "rule_id", "rule_version", "severity", "status", "level", "type", "count"} {
		if value, ok := raw.Payload[key]; ok {
			attributes[key] = value
		}
	}
	if endpoint, ok := raw.Payload["endpoint"].(map[string]any); ok {
		bounded := map[string]any{}
		for _, key := range []string{"os", "arch", "device_id"} {
			if value, exists := endpoint[key]; exists {
				bounded[key] = value
			}
		}
		attributes["endpoint"] = bounded
	}
	// Content and parameters remain local and are explicitly marked for Collector-core removal.
	for _, key := range []string{"content", "reasoning", "parameters", "command", "path", "value", "message", "title", "observed_command", "observed_file_path", "observed_url", "observed_content_preview", "project_path"} {
		if value, ok := raw.Payload[key]; ok {
			attributes[key] = value
		}
	}
	return NormalizedEvent{
		SourceEventID: id, ObservedAt: observed,
		SessionID:     stringValue(raw.Payload["session_id"]),
		AgentID:       stringValue(raw.Payload["agent_id"]),
		DeploymentID:  stringValue(raw.Payload["deployment_id"]),
		OperationName: item.operation, ActionClass: item.action, Billable: item.billable,
		OutcomeStatus: stringValue(raw.Payload["outcome_status"]), Attributes: attributes,
		SensitivePaths:  []string{"content", "reasoning", "parameters", "command", "path", "value", "message", "title", "observed_command", "observed_file_path", "observed_url", "observed_content_preview", "project_path"},
		SourceIntegrity: map[string]any{"source_pin": NumbatCommit, "binary_sha256": NumbatBinarySHA256},
		MappingVersion:  NumbatMappingVersion,
	}, nil
}

func officialNumbatIdentity(payload map[string]any, recordType string) (kind, observed, id string) {
	kind = stringValue(payload["event_type"])
	switch recordType {
	case "event":
		observed, id = stringValue(payload["timestamp"]), stringValue(payload["event_id"])
	case "finding":
		observed, id = stringValue(payload["timestamp"]), stringValue(payload["finding_id"])
		if observed == "" {
			observed = stringValue(payload["detected_at"])
		}
	case "enforcement":
		observed, id = stringValue(payload["timestamp"]), stringValue(payload["decision_id"])
	case "diagnostic":
		observed, id = stringValue(payload["timestamp"]), stringValue(payload["run_id"])
	case "indicator":
		observed = stringValue(payload["last_seen"])
		if observed == "" {
			observed = stringValue(payload["first_seen"])
		}
		if observed == "" {
			observed = "1970-01-01T00:00:00Z"
		}
		id = stringValue(payload["sample_event_id"])
	case "scan_summary":
		observed, id = "1970-01-01T00:00:00Z", stringValue(payload["run_id"])
	default:
		observed = stringValue(payload["timestamp"])
		id = stringValue(payload["event_id"])
		if id == "" {
			id = stringValue(payload["run_id"])
		}
	}
	if kind == "" {
		kind = "numbat." + recordType
	}
	if id == "" && (recordType == "indicator" || recordType == "diagnostic") {
		canonical, _ := json.Marshal(payload)
		sum := sha256.Sum256(canonical)
		id = "numbat-" + hex.EncodeToString(sum[:8])
	}
	return kind, observed, id
}

func stringValue(value any) string {
	text, _ := value.(string)
	return text
}
