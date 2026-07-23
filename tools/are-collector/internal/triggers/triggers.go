// Package triggers implements rescan-on-change detection over runtime evidence
// rows (Evidence Profile v1, backbond.evidence.*). It is the analysis-layer seam
// described in the runtime-evidence-pipeline build spec §3.4.A / build item 7:
// operational drift derived from evidence rows can REQUEST a BackBond rescan, but
// can never move a BackBond Score, band, or any rubric output directly.
//
// Four trigger classes are detected from evidence rows only:
//
//   - ModelRefChange     — model_ref differs from the last observed value (model swap).
//   - AuthorityExpansion — a new tool or permission appears in authority_context.
//   - OutcomeDrift        — failure/escalation/reversal rate over a rolling window
//     crosses a configurable threshold.
//   - ToolchainProvenanceChange — a deployment artifact, dependency set, MCP
//     manifest, workflow identity, inherited credential scope, or contacted
//     infrastructure destination changes.
//
// For each positive trigger the detector appends a RescanRequest to an AuditSpine.
// A RescanRequest is a request only: it carries no score, band, or rubric field
// and there is no code path here that mutates one. Consumption of these requests
// by a scanner release is intentionally out of scope (a separately approved
// release wires the spine to the rescan runtime); this package emits and stops.
//
// Boundary: this package imports only schemav1 and the standard library. It must
// never import scoring, rubric, bands, pricing, portal, or receiver code — the
// collector-wide tests/no_score_imports_test.go enforces this, which is what makes
// "requests rescans only, never writes score changes directly" true by construction.
package triggers

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sort"
	"strings"
	"sync"
)

// TriggerClass enumerates the drift classes that can request a rescan.
type TriggerClass string

const (
	ModelRefChange            TriggerClass = "model_ref_change"
	AuthorityExpansion        TriggerClass = "authority_expansion"
	OutcomeDrift              TriggerClass = "outcome_drift"
	ToolchainProvenanceChange TriggerClass = "toolchain_provenance_change"
)

// EvidenceRow is the trigger-relevant projection of one Evidence Profile v1
// consequential event. It is derived from evidence rows only; see ProjectRow for
// the mapping off backbond.evidence.* attributes. Optional-for-pilot fields
// (ModelRef, AuthorityContext) are empty/nil when the vendor could not emit them,
// and an empty value never fabricates a change.
type EvidenceRow struct {
	TenantID     string
	AgentID      string
	DeploymentID string
	EventTime    string // RFC3339, carried through onto the request for audit ordering

	ModelRef            string
	AuthorityContext    AuthorityContext
	OutcomeStatus       string
	ToolchainProvenance *ToolchainProvenance

	// ContentHash and BatchSeq are chain-of-custody anchors copied onto the
	// emitted request so the audit spine can point back at the triggering row.
	ContentHash string
	BatchSeq    int
}

// ToolchainProvenance carries immutable digests and redacted deployment metadata.
// Credential values and proprietary source material are deliberately unrepresentable.
type ToolchainProvenance struct {
	ArtifactDigest        string
	DependencyDigest      string
	MCPManifestDigest     string
	WorkflowIdentity      string
	InheritedCredentials  []CredentialProvenance
	DeclaredDestinations  []string
	ContactedDestinations []string
}

type CredentialProvenance struct {
	Class  string
	Source string
	Scopes []string
}

// AuthorityContext captures the permissions-in-force view an evidence row carries.
// Tools and Permissions are the sets whose growth signals a governance-relevant
// authority expansion.
type AuthorityContext struct {
	Tools        []string
	Permissions  []string
	ApprovalPath string
	HumanInLoop  bool
}

func (row EvidenceRow) streamKey() string {
	return row.TenantID + "\x1f" + row.AgentID + "\x1f" + row.DeploymentID
}

// OutcomeDriftConfig makes the operational-drift threshold explicit and configurable.
type OutcomeDriftConfig struct {
	// Window is the number of most-recent outcomes retained per stream.
	Window int
	// MinObservations is the minimum window fill before drift can fire, so a
	// single early failure does not request a rescan.
	MinObservations int
	// MaxDriftRate is the inclusive-exclusive ceiling: the trigger fires when the
	// drift rate over the window is strictly greater than this value.
	MaxDriftRate float64
	// DriftStatuses are the outcome_status values counted as drift. Defaults to
	// failed/reversed/escalated per spec §3.4.A ("failure/escalation/reversal").
	DriftStatuses []string
}

// Config is the trigger-evaluation configuration. Enabled is the rollback gate:
// when false, evidence ingestion (baseline tracking) continues unchanged but no
// rescan-request is emitted or returned.
type Config struct {
	Enabled                    bool
	ToolchainProvenanceEnabled bool
	OutcomeDrift               OutcomeDriftConfig
}

// DefaultConfig returns explicit, conservative defaults. Every threshold is a
// named field so operators tune them without code changes.
func DefaultConfig() Config {
	return Config{
		Enabled:                    true,
		ToolchainProvenanceEnabled: true,
		OutcomeDrift: OutcomeDriftConfig{
			Window:          20,
			MinObservations: 10,
			MaxDriftRate:    0.25,
			DriftStatuses:   []string{"failed", "reversed", "escalated"},
		},
	}
}

// RescanRequest is the event appended to the audit spine on a positive trigger.
// It is a REQUEST: it carries no score, band, or rubric field, and nothing in this
// package can move one. RequestOnly is always true and exists to make that
// contract legible to consumers and reviewers.
type RescanRequest struct {
	RequestID    string // deterministic; identical triggering evidence yields the same id (replay-safe)
	TriggerClass TriggerClass
	TenantID     string
	AgentID      string
	DeploymentID string
	DetectedAt   string // EventTime of the triggering evidence row

	// Reason is a deterministic human-readable summary of the drift.
	Reason string
	// Details carries the class-specific before/after values.
	Details map[string]string
	// EvidenceRefs anchors the request to the triggering evidence row.
	EvidenceRefs EvidenceRefs

	// RequestOnly is always true: this event requests a rescan and never moves a score.
	RequestOnly bool
}

// EvidenceRefs is the chain-of-custody pointer back to the triggering row.
type EvidenceRefs struct {
	ContentHash string
	BatchSeq    int
}

// AuditSpine is the append-only sink for rescan-request events. Implementations
// append; they never update or delete, mirroring the evidence-store posture.
type AuditSpine interface {
	EmitRescanRequest(req RescanRequest) error
}

// RecordingSpine is an in-memory, append-only AuditSpine. It is the default sink
// and the fixture used by tests; a scanner release supplies a durable spine later.
type RecordingSpine struct {
	mu       sync.Mutex
	requests []RescanRequest
}

// EmitRescanRequest appends req. It never mutates or removes a prior request.
func (spine *RecordingSpine) EmitRescanRequest(req RescanRequest) error {
	spine.mu.Lock()
	defer spine.mu.Unlock()
	spine.requests = append(spine.requests, req)
	return nil
}

// Requests returns a copy of the appended requests in emission order.
func (spine *RecordingSpine) Requests() []RescanRequest {
	spine.mu.Lock()
	defer spine.mu.Unlock()
	out := make([]RescanRequest, len(spine.requests))
	copy(out, spine.requests)
	return out
}

type streamState struct {
	seededModel bool
	lastModel   string

	seededAuthority bool
	knownTools      map[string]struct{}
	knownPerms      map[string]struct{}

	outcomes    []string
	driftFiring bool

	seededToolchain bool
	lastToolchain   string
}

// Detector holds per-(tenant, agent, deployment) baseline state and evaluates
// evidence rows against the three trigger classes. It is safe for concurrent use.
type Detector struct {
	mu      sync.Mutex
	cfg     Config
	spine   AuditSpine
	streams map[string]*streamState
}

// NewDetector builds a Detector that appends to spine using cfg.
func NewDetector(cfg Config, spine AuditSpine) *Detector {
	return &Detector{
		cfg:     cfg,
		spine:   spine,
		streams: map[string]*streamState{},
	}
}

func (detector *Detector) streamFor(key string) *streamState {
	existing := detector.streams[key]
	if existing == nil {
		existing = &streamState{
			knownTools: map[string]struct{}{},
			knownPerms: map[string]struct{}{},
		}
		detector.streams[key] = existing
	}
	return existing
}

// Observe records one evidence row, updates baselines, and returns the rescan
// requests it triggered. Baseline tracking always runs so that toggling the
// rollback gate never replays historical drift. When cfg.Enabled is false, no
// request is emitted to the spine or returned; ingestion is otherwise unchanged.
func (detector *Detector) Observe(row EvidenceRow) ([]RescanRequest, error) {
	if row.TenantID == "" || row.AgentID == "" || row.DeploymentID == "" {
		return nil, fmt.Errorf("evidence row is missing tenant/agent/deployment identity")
	}

	detector.mu.Lock()
	defer detector.mu.Unlock()

	state := detector.streamFor(row.streamKey())
	requests := []RescanRequest{}

	if req, ok := detector.evalModelRef(state, row); ok {
		requests = append(requests, req)
	}
	if req, ok := detector.evalAuthority(state, row); ok {
		requests = append(requests, req)
	}
	if req, ok := detector.evalOutcomeDrift(state, row); ok {
		requests = append(requests, req)
	}
	if req, ok, err := detector.evalToolchainProvenance(state, row); err != nil {
		return nil, err
	} else if ok {
		requests = append(requests, req)
	}

	if !detector.cfg.Enabled {
		// Rollback gate: baselines advanced above (ingestion unchanged) but no
		// rescan-request leaves this detector.
		return nil, nil
	}

	for _, req := range requests {
		if err := detector.spine.EmitRescanRequest(req); err != nil {
			return nil, err
		}
	}
	return requests, nil
}

func (detector *Detector) evalToolchainProvenance(state *streamState, row EvidenceRow) (RescanRequest, bool, error) {
	provenance := row.ToolchainProvenance
	if provenance == nil {
		return RescanRequest{}, false, nil
	}
	if provenance.ArtifactDigest == "" || provenance.DependencyDigest == "" ||
		provenance.MCPManifestDigest == "" || provenance.WorkflowIdentity == "" {
		return RescanRequest{}, false, fmt.Errorf("toolchain provenance is incomplete")
	}
	fingerprint, details, unexpected := toolchainFingerprint(*provenance)
	if !state.seededToolchain {
		state.seededToolchain = true
		state.lastToolchain = fingerprint
		if len(unexpected) == 0 || !detector.cfg.ToolchainProvenanceEnabled {
			return RescanRequest{}, false, nil
		}
		details["new_destinations"] = strings.Join(unexpected, ",")
		return detector.newRequest(ToolchainProvenanceChange, row,
			"toolchain provenance contacted undeclared destinations ["+strings.Join(unexpected, ", ")+"]", details), true, nil
	}
	if fingerprint == state.lastToolchain {
		return RescanRequest{}, false, nil
	}
	previous := state.lastToolchain
	state.lastToolchain = fingerprint
	if !detector.cfg.ToolchainProvenanceEnabled {
		return RescanRequest{}, false, nil
	}
	details["previous_fingerprint"] = previous
	details["current_fingerprint"] = fingerprint
	if len(unexpected) > 0 {
		details["new_destinations"] = strings.Join(unexpected, ",")
	}
	return detector.newRequest(ToolchainProvenanceChange, row,
		"deployment toolchain provenance changed", details), true, nil
}

func toolchainFingerprint(provenance ToolchainProvenance) (string, map[string]string, []string) {
	credentials := make([]string, 0, len(provenance.InheritedCredentials))
	for _, credential := range provenance.InheritedCredentials {
		scopes := normalized(credential.Scopes)
		credentials = append(credentials, credential.Class+"\x1e"+credential.Source+"\x1e"+strings.Join(scopes, ","))
	}
	sort.Strings(credentials)
	declared := normalized(provenance.DeclaredDestinations)
	contacted := normalized(provenance.ContactedDestinations)
	allowlist := map[string]struct{}{}
	for _, destination := range declared {
		allowlist[destination] = struct{}{}
	}
	unexpected := []string{}
	for _, destination := range contacted {
		if _, ok := allowlist[destination]; !ok {
			unexpected = append(unexpected, destination)
		}
	}
	preimage := strings.Join([]string{
		provenance.ArtifactDigest,
		provenance.DependencyDigest,
		provenance.MCPManifestDigest,
		provenance.WorkflowIdentity,
		strings.Join(credentials, "\x1d"),
		strings.Join(declared, "\x1d"),
		strings.Join(contacted, "\x1d"),
	}, "\x1f")
	sum := sha256.Sum256([]byte(preimage))
	fingerprint := "sha256:" + hex.EncodeToString(sum[:])
	return fingerprint, map[string]string{
		"artifact_digest":     provenance.ArtifactDigest,
		"dependency_digest":   provenance.DependencyDigest,
		"mcp_manifest_digest": provenance.MCPManifestDigest,
		"workflow_identity":   provenance.WorkflowIdentity,
	}, unexpected
}

func normalized(values []string) []string {
	seen := map[string]struct{}{}
	for _, value := range values {
		value = strings.ToLower(strings.TrimSpace(value))
		if value != "" {
			seen[value] = struct{}{}
		}
	}
	out := make([]string, 0, len(seen))
	for value := range seen {
		out = append(out, value)
	}
	sort.Strings(out)
	return out
}

func (detector *Detector) evalModelRef(state *streamState, row EvidenceRow) (RescanRequest, bool) {
	if row.ModelRef == "" {
		// Optional-for-pilot and absent: never fabricate a change.
		return RescanRequest{}, false
	}
	if !state.seededModel {
		state.seededModel = true
		state.lastModel = row.ModelRef
		return RescanRequest{}, false
	}
	if row.ModelRef == state.lastModel {
		return RescanRequest{}, false
	}
	previous := state.lastModel
	state.lastModel = row.ModelRef
	return detector.newRequest(ModelRefChange, row,
		fmt.Sprintf("model_ref changed from %q to %q", previous, row.ModelRef),
		map[string]string{"previous_model_ref": previous, "current_model_ref": row.ModelRef}), true
}

func (detector *Detector) evalAuthority(state *streamState, row EvidenceRow) (RescanRequest, bool) {
	newTools := unseen(state.knownTools, row.AuthorityContext.Tools)
	newPerms := unseen(state.knownPerms, row.AuthorityContext.Permissions)

	// Seed the baseline from the first row that actually carries authority context;
	// the initial known set is never itself an expansion.
	firstAuthorityRow := !state.seededAuthority &&
		(len(row.AuthorityContext.Tools) > 0 || len(row.AuthorityContext.Permissions) > 0)
	if firstAuthorityRow {
		state.seededAuthority = true
		absorb(state.knownTools, newTools)
		absorb(state.knownPerms, newPerms)
		return RescanRequest{}, false
	}

	if len(newTools) == 0 && len(newPerms) == 0 {
		return RescanRequest{}, false
	}
	absorb(state.knownTools, newTools)
	absorb(state.knownPerms, newPerms)

	details := map[string]string{}
	parts := []string{}
	if len(newTools) > 0 {
		details["new_tools"] = strings.Join(newTools, ",")
		parts = append(parts, "tools ["+strings.Join(newTools, ", ")+"]")
	}
	if len(newPerms) > 0 {
		details["new_permissions"] = strings.Join(newPerms, ",")
		parts = append(parts, "permissions ["+strings.Join(newPerms, ", ")+"]")
	}
	return detector.newRequest(AuthorityExpansion, row,
		"authority_context expanded: "+strings.Join(parts, "; "), details), true
}

func (detector *Detector) evalOutcomeDrift(state *streamState, row EvidenceRow) (RescanRequest, bool) {
	if row.OutcomeStatus == "" {
		return RescanRequest{}, false
	}
	cfg := detector.cfg.OutcomeDrift
	state.outcomes = append(state.outcomes, row.OutcomeStatus)
	if cfg.Window > 0 && len(state.outcomes) > cfg.Window {
		state.outcomes = state.outcomes[len(state.outcomes)-cfg.Window:]
	}

	if len(state.outcomes) < cfg.MinObservations {
		return RescanRequest{}, false
	}

	drift := 0
	for _, outcome := range state.outcomes {
		if isDrift(outcome, cfg.DriftStatuses) {
			drift++
		}
	}
	rate := float64(drift) / float64(len(state.outcomes))

	if rate <= cfg.MaxDriftRate {
		// Rate recovered: allow a future breach to fire again.
		state.driftFiring = false
		return RescanRequest{}, false
	}
	if state.driftFiring {
		// Already reported this breach episode; do not restorm on every row.
		return RescanRequest{}, false
	}
	state.driftFiring = true
	details := map[string]string{
		"drift_count":    fmt.Sprintf("%d", drift),
		"window_size":    fmt.Sprintf("%d", len(state.outcomes)),
		"drift_rate":     fmt.Sprintf("%.4f", rate),
		"max_drift_rate": fmt.Sprintf("%.4f", cfg.MaxDriftRate),
	}
	return detector.newRequest(OutcomeDrift, row,
		fmt.Sprintf("failure/escalation/reversal drift %.4f > threshold %.4f over %d events",
			rate, cfg.MaxDriftRate, len(state.outcomes)), details), true
}

func (detector *Detector) newRequest(class TriggerClass, row EvidenceRow, reason string, details map[string]string) RescanRequest {
	return RescanRequest{
		RequestID:    requestID(class, row, details),
		TriggerClass: class,
		TenantID:     row.TenantID,
		AgentID:      row.AgentID,
		DeploymentID: row.DeploymentID,
		DetectedAt:   row.EventTime,
		Reason:       reason,
		Details:      details,
		EvidenceRefs: EvidenceRefs{ContentHash: row.ContentHash, BatchSeq: row.BatchSeq},
		RequestOnly:  true,
	}
}

// requestID is deterministic in the triggering evidence, so a replayed row yields
// the same id and a spine consumer can dedupe rather than double-request.
func requestID(class TriggerClass, row EvidenceRow, details map[string]string) string {
	keys := make([]string, 0, len(details))
	for key := range details {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	builder := strings.Builder{}
	builder.WriteString(string(class))
	builder.WriteString("\x1f" + row.streamKey())
	builder.WriteString("\x1f" + row.EventTime)
	builder.WriteString("\x1f" + row.ContentHash)
	for _, key := range keys {
		builder.WriteString("\x1f" + key + "=" + details[key])
	}
	digest := sha256.Sum256([]byte(builder.String()))
	return "rescan-req-" + hex.EncodeToString(digest[:16])
}

// unseen returns, sorted and de-duplicated, the values not already in known.
func unseen(known map[string]struct{}, values []string) []string {
	out := []string{}
	seen := map[string]struct{}{}
	for _, value := range values {
		if value == "" {
			continue
		}
		if _, ok := known[value]; ok {
			continue
		}
		if _, ok := seen[value]; ok {
			continue
		}
		seen[value] = struct{}{}
		out = append(out, value)
	}
	sort.Strings(out)
	return out
}

func absorb(known map[string]struct{}, values []string) {
	for _, value := range values {
		known[value] = struct{}{}
	}
}

func isDrift(outcome string, driftStatuses []string) bool {
	for _, status := range driftStatuses {
		if outcome == status {
			return true
		}
	}
	return false
}
