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
	NumbatCommit         = "3d20d782d45001fd3bb200bc5690ce4b9ce0f12b"
	NumbatBinarySHA256   = "2e95ac4909db80bfb74aa77f6bed8104173d411a1614bd831e8e65484ef4ecde"
	NumbatMappingVersion = "numbat-map-v0.1"
)

type NumbatSourceDriver struct {
	records    []RawEnvelope
	checkpoint string
}

func NewNumbatSourceDriver(reader io.Reader) (*NumbatSourceDriver, error) {
	driver := &NumbatSourceDriver{}
	scanner := bufio.NewScanner(reader)
	for scanner.Scan() {
		if strings.TrimSpace(scanner.Text()) == "" {
			continue
		}
		var payload map[string]any
		if err := json.Unmarshal(scanner.Bytes(), &payload); err != nil {
			return nil, fmt.Errorf("numbat ndjson: %w", err)
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
	kind, _ := raw.Payload["event_type"].(string)
	observed, _ := raw.Payload["observed_at"].(string)
	if kind == "" || observed == "" {
		return NormalizedEvent{}, fmt.Errorf("numbat record requires event_type and observed_at")
	}
	item, known := numbatMappings[kind]
	if !known {
		item = mapping{"invoke_agent", "message", false, "agent_invocation"}
	}
	id, _ := raw.Payload["event_id"].(string)
	if id == "" {
		canonical, _ := json.Marshal(raw.Payload)
		sum := sha256.Sum256(canonical)
		id = "numbat-" + hex.EncodeToString(sum[:8])
	}
	attributes := map[string]any{
		"numbat.event_type": kind,
		"numbat.known_kind": known,
		"schema_event_type": item.eventType,
	}
	for _, key := range []string{"call_id", "parent_call_id", "outcome_status", "session_id", "agent_id", "deployment_id"} {
		if value, ok := raw.Payload[key]; ok {
			attributes[key] = value
		}
	}
	// Content and parameters remain local and are explicitly marked for Collector-core removal.
	for _, key := range []string{"content", "reasoning", "parameters", "command", "path"} {
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
		SensitivePaths:  []string{"content", "reasoning", "parameters", "command", "path"},
		SourceIntegrity: map[string]any{"source_pin": NumbatCommit, "binary_sha256": NumbatBinarySHA256},
		MappingVersion:  NumbatMappingVersion,
	}, nil
}

func stringValue(value any) string {
	text, _ := value.(string)
	return text
}
