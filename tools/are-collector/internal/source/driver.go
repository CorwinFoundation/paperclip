// Package source defines the versioned, sensor-agnostic Collector input boundary.
package source

import "context"

const InterfaceVersion = "source-driver-v0.1"

type EvidenceQuality string

const (
	EvidenceSelfReported       EvidenceQuality = "self_reported"
	EvidenceGateway            EvidenceQuality = "gateway"
	EvidenceEndpointADR        EvidenceQuality = "endpoint_adr"
	EvidenceKernelCorroborated EvidenceQuality = "kernel_corroborated"
)

type Descriptor struct {
	DriverID        string          `json:"driver_id"`
	DriverVersion   string          `json:"driver_version"`
	SourceClass     string          `json:"source_class"`
	SourcePin       string          `json:"source_pin"`
	EvidenceQuality EvidenceQuality `json:"evidence_quality"`
}

type RawEnvelope struct {
	Offset  string
	Payload map[string]any
}

type NormalizedEvent struct {
	SourceEventID   string
	ObservedAt      string
	SessionID       string
	AgentID         string
	DeploymentID    string
	OperationName   string
	ActionClass     string
	Billable        bool
	OutcomeStatus   string
	Attributes      map[string]any
	SensitivePaths  []string
	SourceIntegrity map[string]any
	MappingVersion  string
}

type SourceDriver interface {
	Descriptor() Descriptor
	Checkpoint(context.Context) (string, error)
	Acquire(context.Context, string, int) ([]RawEnvelope, string, error)
	Normalize(RawEnvelope) (NormalizedEvent, error)
	Commit(context.Context, string) error
}
