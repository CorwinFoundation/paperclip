package source

import (
	"bufio"
	"bytes"
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const SyntheticVoiceMetadataMappingVersion = "synthetic-voice-metadata-map-v1.0"

var (
	// Identifiers are independently minted UUIDv4 values. Producers must never
	// derive, hash, or encode telephone or PII metadata into these fields.
	syntheticVoiceOpaqueID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)
	// Action classes are the Evidence Profile v1 vocabulary pinned by
	// contracts/standard-agent-interface-v1.md.
	syntheticVoiceActionClasses = map[string]struct{}{
		"message_send": {}, "transaction": {}, "data_access": {}, "tool_call": {},
		"human_escalation": {}, "admin_change": {}, "other": {},
	}
	// Outcome statuses are the complete collector drift vocabulary exercised by
	// internal/triggers and its fixtures.
	syntheticVoiceOutcomeStatuses = map[string]struct{}{
		"completed": {}, "failed": {}, "reversed": {}, "escalated": {},
	}
	// synthetic-voice-metadata-map-v1.0 currently approves one aggregate
	// determination, as pinned by the source and pipeline fixtures.
	syntheticVoiceDeterminationClasses = map[string]struct{}{"availability": {}}
	syntheticVoiceDeterminationResults = map[string]struct{}{"pass": {}, "fail": {}}
)

type SyntheticVoiceDetermination struct {
	Class  string `json:"class"`
	Result string `json:"result"`
}

type SyntheticVoiceCommitments struct {
	EvidenceSHA256 string `json:"evidence_sha256"`
}

type SyntheticVoiceContinuity struct {
	Sequence            int    `json:"sequence"`
	PreviousEventSHA256 string `json:"previous_event_sha256"`
}

type SyntheticVoiceProducer struct {
	CollectorVersion string `json:"collector_version"`
	DriverVersion    string `json:"driver_version"`
	ScannerVersion   string `json:"scanner_version"`
	CorpusVersion    string `json:"corpus_version"`
	Seed             int    `json:"seed"`
}

type SyntheticVoiceMetadataEvent struct {
	SourceEventID  string                        `json:"source_event_id"`
	ObservedAt     string                        `json:"observed_at"`
	AgentID        string                        `json:"agent_id"`
	DeploymentID   string                        `json:"deployment_id"`
	Count          int                           `json:"count"`
	ActionClass    string                        `json:"action_class"`
	OutcomeStatus  string                        `json:"outcome_status"`
	Determinations []SyntheticVoiceDetermination `json:"determinations"`
	Commitments    SyntheticVoiceCommitments     `json:"commitments"`
	Continuity     SyntheticVoiceContinuity      `json:"continuity"`
	Producer       SyntheticVoiceProducer        `json:"producer"`
}

type syntheticVoiceEnvelope struct {
	KeyID     string          `json:"key_id"`
	Event     json.RawMessage `json:"event"`
	Signature string          `json:"signature"`
}

type SyntheticVoiceMetadataSourceDriver struct {
	records    []RawEnvelope
	checkpoint string
}

func NewSyntheticVoiceMetadataSourceDriver(reader io.Reader, keyring map[string]ed25519.PublicKey) (*SyntheticVoiceMetadataSourceDriver, error) {
	driver := &SyntheticVoiceMetadataSourceDriver{}
	seen := map[string]struct{}{}
	scanner := bufio.NewScanner(reader)
	for scanner.Scan() {
		line := bytes.TrimSpace(scanner.Bytes())
		if len(line) == 0 {
			continue
		}
		var envelope syntheticVoiceEnvelope
		if err := decodeClosed(line, &envelope); err != nil {
			return nil, fmt.Errorf("synthetic voice envelope: %w", err)
		}
		publicKey, ok := keyring[envelope.KeyID]
		if !ok || len(publicKey) != ed25519.PublicKeySize {
			return nil, fmt.Errorf("unknown synthetic voice signing key %q", envelope.KeyID)
		}
		var event SyntheticVoiceMetadataEvent
		if err := decodeClosed(envelope.Event, &event); err != nil {
			return nil, fmt.Errorf("synthetic voice event: %w", err)
		}
		if err := validateSyntheticVoiceEvent(event); err != nil {
			return nil, err
		}
		var canonicalValue any
		if err := json.Unmarshal(envelope.Event, &canonicalValue); err != nil {
			return nil, err
		}
		canonical, err := json.Marshal(canonicalValue)
		if err != nil {
			return nil, err
		}
		signature, err := base64.RawURLEncoding.DecodeString(envelope.Signature)
		if err != nil || !ed25519.Verify(publicKey, canonical, signature) {
			return nil, fmt.Errorf("invalid synthetic voice event signature")
		}
		if _, duplicate := seen[event.SourceEventID]; duplicate {
			continue
		}
		seen[event.SourceEventID] = struct{}{}
		var payload map[string]any
		if err := json.Unmarshal(envelope.Event, &payload); err != nil {
			return nil, err
		}
		driver.records = append(driver.records, RawEnvelope{Offset: strconv.Itoa(len(driver.records) + 1), Payload: payload})
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	return driver, nil
}

func decodeClosed(raw []byte, target any) error {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	if decoder.More() {
		return fmt.Errorf("multiple JSON values are not allowed")
	}
	return nil
}

func validateSyntheticVoiceEvent(event SyntheticVoiceMetadataEvent) error {
	for name, value := range map[string]string{
		"source_event_id": event.SourceEventID,
		"agent_id":        event.AgentID,
		"deployment_id":   event.DeploymentID,
	} {
		if !syntheticVoiceOpaqueID.MatchString(value) {
			return fmt.Errorf("%s must be an independently minted canonical lowercase UUIDv4", name)
		}
	}
	if _, err := time.Parse(time.RFC3339, event.ObservedAt); err != nil {
		return fmt.Errorf("observed_at must be RFC3339: %w", err)
	}
	if event.Count < 1 {
		return fmt.Errorf("positive count is required")
	}
	if _, ok := syntheticVoiceActionClasses[event.ActionClass]; !ok {
		return fmt.Errorf("action_class is not in the Evidence Profile vocabulary")
	}
	if _, ok := syntheticVoiceOutcomeStatuses[event.OutcomeStatus]; !ok {
		return fmt.Errorf("outcome_status is not in the synthetic voice mapping vocabulary")
	}
	if len(event.Determinations) == 0 {
		return fmt.Errorf("at least one determination is required")
	}
	for _, determination := range event.Determinations {
		if _, ok := syntheticVoiceDeterminationClasses[determination.Class]; !ok {
			return fmt.Errorf("determination class is not in the synthetic voice mapping vocabulary")
		}
		if _, ok := syntheticVoiceDeterminationResults[determination.Result]; !ok {
			return fmt.Errorf("determination result is not in the synthetic voice mapping vocabulary")
		}
	}
	for name, value := range map[string]string{"commitments.evidence_sha256": event.Commitments.EvidenceSHA256, "continuity.previous_event_sha256": event.Continuity.PreviousEventSHA256} {
		if !closedSHA256(value) {
			return fmt.Errorf("%s must use sha256:<64 lowercase hex>", name)
		}
	}
	if event.Continuity.Sequence < 1 {
		return fmt.Errorf("continuity.sequence must be positive")
	}
	producer := event.Producer
	if producer.CollectorVersion == "" || producer.DriverVersion != SyntheticVoiceMetadataMappingVersion || producer.ScannerVersion == "" || producer.CorpusVersion == "" {
		return fmt.Errorf("complete, pinned producer metadata is required")
	}
	return nil
}

func closedSHA256(value string) bool {
	if len(value) != 71 || !strings.HasPrefix(value, "sha256:") || value != strings.ToLower(value) {
		return false
	}
	_, err := hex.DecodeString(strings.TrimPrefix(value, "sha256:"))
	return err == nil
}

func (driver *SyntheticVoiceMetadataSourceDriver) Descriptor() Descriptor {
	return Descriptor{DriverID: "synthetic_voice_metadata", DriverVersion: InterfaceVersion, SourceClass: "synthetic_voice_metadata", SourcePin: SyntheticVoiceMetadataMappingVersion, EvidenceQuality: EvidenceGateway}
}

func (driver *SyntheticVoiceMetadataSourceDriver) Checkpoint(context.Context) (string, error) {
	return driver.checkpoint, nil
}

func (driver *SyntheticVoiceMetadataSourceDriver) Acquire(_ context.Context, after string, max int) ([]RawEnvelope, string, error) {
	start := 0
	if after != "" {
		parsed, err := strconv.Atoi(after)
		if err != nil || parsed < 0 || parsed > len(driver.records) {
			return nil, "", fmt.Errorf("invalid synthetic voice checkpoint %q", after)
		}
		start = parsed
	}
	end := len(driver.records)
	if max > 0 && start+max < end {
		end = start + max
	}
	return append([]RawEnvelope(nil), driver.records[start:end]...), strconv.Itoa(end), nil
}

func (driver *SyntheticVoiceMetadataSourceDriver) Commit(_ context.Context, checkpoint string) error {
	parsed, err := strconv.Atoi(checkpoint)
	if err != nil || parsed < 0 || parsed > len(driver.records) {
		return fmt.Errorf("invalid synthetic voice checkpoint %q", checkpoint)
	}
	driver.checkpoint = checkpoint
	return nil
}

func (driver *SyntheticVoiceMetadataSourceDriver) Normalize(raw RawEnvelope) (NormalizedEvent, error) {
	encoded, err := json.Marshal(raw.Payload)
	if err != nil {
		return NormalizedEvent{}, err
	}
	var event SyntheticVoiceMetadataEvent
	if err := decodeClosed(encoded, &event); err != nil {
		return NormalizedEvent{}, err
	}
	if err := validateSyntheticVoiceEvent(event); err != nil {
		return NormalizedEvent{}, err
	}
	return NormalizedEvent{
		SourceEventID: event.SourceEventID, ObservedAt: event.ObservedAt, AgentID: event.AgentID,
		DeploymentID: event.DeploymentID, OperationName: "synthetic_voice_metadata", ActionClass: event.ActionClass,
		Billable: false, OutcomeStatus: event.OutcomeStatus, MappingVersion: SyntheticVoiceMetadataMappingVersion,
		SourceIntegrity: map[string]any{"signature_verified": true},
		Attributes:      map[string]any{"schema_event_type": "agent_invocation", "count": event.Count, "determinations": event.Determinations, "commitments": event.Commitments, "continuity": event.Continuity, "producer": event.Producer},
	}, nil
}
