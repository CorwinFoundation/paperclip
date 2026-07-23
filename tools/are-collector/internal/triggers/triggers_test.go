package triggers

import (
	"reflect"
	"strings"
	"testing"
)

func row(model string, tools, perms []string, outcome string) EvidenceRow {
	return EvidenceRow{
		TenantID:         "t1",
		AgentID:          "a1",
		DeploymentID:     "prod",
		EventTime:        "2026-07-03T00:00:00Z",
		ModelRef:         model,
		AuthorityContext: AuthorityContext{Tools: tools, Permissions: perms},
		OutcomeStatus:    outcome,
		ContentHash:      "sha256:" + strings.Repeat("a", 64),
		BatchSeq:         7,
	}
}

// Acceptance: model_ref change is detected from evidence rows and requests a rescan.
func TestModelRefChangeFiresFromEvidenceRows(t *testing.T) {
	spine := &RecordingSpine{}
	detector := NewDetector(DefaultConfig(), spine)

	if reqs, _ := detector.Observe(row("gpt-4o", nil, nil, "")); len(reqs) != 0 {
		t.Fatalf("first row seeds the model baseline, must not trigger: %+v", reqs)
	}
	if reqs, _ := detector.Observe(row("gpt-4o", nil, nil, "")); len(reqs) != 0 {
		t.Fatalf("same model_ref must not trigger: %+v", reqs)
	}
	reqs, err := detector.Observe(row("claude-opus-4-8", nil, nil, ""))
	if err != nil {
		t.Fatal(err)
	}
	if len(reqs) != 1 || reqs[0].TriggerClass != ModelRefChange {
		t.Fatalf("expected one model_ref_change request, got %+v", reqs)
	}
	emitted := spine.Requests()
	if len(emitted) != 1 || emitted[0].TriggerClass != ModelRefChange {
		t.Fatalf("expected the request appended to the audit spine, got %+v", emitted)
	}
	if emitted[0].Details["previous_model_ref"] != "gpt-4o" || emitted[0].Details["current_model_ref"] != "claude-opus-4-8" {
		t.Fatalf("expected before/after model_ref in details, got %+v", emitted[0].Details)
	}
}

// Acceptance: a new tool or permission in authority_context is detected from
// evidence rows and requests a rescan.
func TestAuthorityExpansionFiresFromEvidenceRows(t *testing.T) {
	spine := &RecordingSpine{}
	detector := NewDetector(DefaultConfig(), spine)

	if reqs, _ := detector.Observe(row("", []string{"send_email"}, []string{"read"}, "")); len(reqs) != 0 {
		t.Fatalf("first authority row seeds the baseline, must not trigger: %+v", reqs)
	}
	if reqs, _ := detector.Observe(row("", []string{"send_email"}, []string{"read"}, "")); len(reqs) != 0 {
		t.Fatalf("unchanged authority must not trigger: %+v", reqs)
	}

	toolReqs, _ := detector.Observe(row("", []string{"send_email", "wire_transfer"}, []string{"read"}, ""))
	if len(toolReqs) != 1 || toolReqs[0].TriggerClass != AuthorityExpansion {
		t.Fatalf("expected authority_expansion on new tool, got %+v", toolReqs)
	}
	if toolReqs[0].Details["new_tools"] != "wire_transfer" {
		t.Fatalf("expected new_tools=wire_transfer, got %+v", toolReqs[0].Details)
	}

	permReqs, _ := detector.Observe(row("", []string{"send_email", "wire_transfer"}, []string{"read", "write"}, ""))
	if len(permReqs) != 1 || permReqs[0].Details["new_permissions"] != "write" {
		t.Fatalf("expected authority_expansion on new permission, got %+v", permReqs)
	}
	if len(spine.Requests()) != 2 {
		t.Fatalf("expected two requests appended to the spine, got %d", len(spine.Requests()))
	}
}

// Acceptance: failure/escalation/reversal drift past a configurable threshold is
// detected from evidence rows; drift below the threshold is not.
func TestOutcomeDriftFiresPastThreshold(t *testing.T) {
	spine := &RecordingSpine{}
	cfg := Config{
		Enabled: true,
		OutcomeDrift: OutcomeDriftConfig{
			Window:          10,
			MinObservations: 10,
			MaxDriftRate:    0.30,
			DriftStatuses:   []string{"failed", "reversed", "escalated"},
		},
	}
	detector := NewDetector(cfg, spine)

	// 10 events, 2 drift (20%) — below the 30% threshold, no request.
	feed := []string{"completed", "failed", "completed", "completed", "escalated",
		"completed", "completed", "completed", "completed", "completed"}
	for _, outcome := range feed {
		if reqs, _ := detector.Observe(row("", nil, nil, outcome)); len(reqs) != 0 {
			t.Fatalf("20%% drift must stay under 30%% threshold, got %+v", reqs)
		}
	}

	// Push the rolling window past the threshold: a burst of failures replaces
	// completed slots until drift exceeds 30%.
	fired := 0
	var firstClass TriggerClass
	for _, outcome := range []string{"failed", "reversed", "failed", "escalated"} {
		reqs, _ := detector.Observe(row("", nil, nil, outcome))
		if len(reqs) > 0 {
			fired += len(reqs)
			if firstClass == "" {
				firstClass = reqs[0].TriggerClass
			}
		}
	}
	if fired == 0 {
		t.Fatal("expected outcome_drift to fire once the window crosses the threshold")
	}
	if firstClass != OutcomeDrift {
		t.Fatalf("expected outcome_drift class, got %s", firstClass)
	}
	// Replay-safety: a single sustained breach episode does not restorm on every row.
	if fired > 1 {
		t.Fatalf("a single breach episode should request a rescan once, fired %d times", fired)
	}
}

// Below the configured MinObservations, an early failure must not request a rescan.
func TestOutcomeDriftRespectsMinObservations(t *testing.T) {
	spine := &RecordingSpine{}
	cfg := DefaultConfig()
	detector := NewDetector(cfg, spine)
	// Two straight failures = 100% drift, but under MinObservations (10).
	detector.Observe(row("", nil, nil, "failed"))
	if reqs, _ := detector.Observe(row("", nil, nil, "failed")); len(reqs) != 0 {
		t.Fatalf("must not fire before MinObservations reached, got %+v", reqs)
	}
	if len(spine.Requests()) != 0 {
		t.Fatal("no request should reach the spine before MinObservations")
	}
}

// Thresholds are configurable: the same evidence stream fires under a strict
// config and stays silent under a lenient one.
func TestThresholdsAreConfigurable(t *testing.T) {
	feed := []string{"completed", "failed", "completed", "failed", "completed",
		"failed", "completed", "completed", "completed", "completed"} // 30% drift

	strict := NewDetector(Config{Enabled: true, OutcomeDrift: OutcomeDriftConfig{
		Window: 10, MinObservations: 10, MaxDriftRate: 0.20,
		DriftStatuses: []string{"failed", "reversed", "escalated"}}}, &RecordingSpine{})
	lenient := NewDetector(Config{Enabled: true, OutcomeDrift: OutcomeDriftConfig{
		Window: 10, MinObservations: 10, MaxDriftRate: 0.50,
		DriftStatuses: []string{"failed", "reversed", "escalated"}}}, &RecordingSpine{})

	strictFired, lenientFired := 0, 0
	for _, outcome := range feed {
		if reqs, _ := strict.Observe(row("", nil, nil, outcome)); len(reqs) > 0 {
			strictFired += len(reqs)
		}
		if reqs, _ := lenient.Observe(row("", nil, nil, outcome)); len(reqs) > 0 {
			lenientFired += len(reqs)
		}
	}
	if strictFired == 0 {
		t.Fatal("strict threshold (0.20) should fire on 30% drift")
	}
	if lenientFired != 0 {
		t.Fatalf("lenient threshold (0.50) should not fire on 30%% drift, fired %d", lenientFired)
	}
}

// The rollback gate suppresses emission while leaving evidence ingestion (baseline
// tracking) unchanged.
func TestRollbackGateSuppressesEmissionButNotIngestion(t *testing.T) {
	spine := &RecordingSpine{}
	cfg := DefaultConfig()
	cfg.Enabled = false
	detector := NewDetector(cfg, spine)

	// A model swap during the disabled window advances the baseline but emits nothing.
	detector.Observe(row("gpt-4o", nil, nil, ""))
	reqs, _ := detector.Observe(row("claude-opus-4-8", nil, nil, ""))
	if len(reqs) != 0 {
		t.Fatalf("disabled detector must not return requests, got %+v", reqs)
	}
	if len(spine.Requests()) != 0 {
		t.Fatal("disabled detector must not emit to the spine")
	}

	// Re-enable: the baseline advanced to claude-opus-4-8 during the disabled
	// window, so re-observing it is not a change (ingestion was not lost).
	detector.cfg.Enabled = true
	if reqs, _ := detector.Observe(row("claude-opus-4-8", nil, nil, "")); len(reqs) != 0 {
		t.Fatalf("baseline should have advanced while disabled, got %+v", reqs)
	}
}

// Triggers request rescans only: the emitted event carries no score/band/rubric
// field and RequestOnly is always true.
func TestRequestsAreRescanRequestsOnly(t *testing.T) {
	spine := &RecordingSpine{}
	detector := NewDetector(DefaultConfig(), spine)
	detector.Observe(row("gpt-4o", nil, nil, ""))
	detector.Observe(row("claude-opus-4-8", nil, nil, ""))

	emitted := spine.Requests()
	if len(emitted) != 1 {
		t.Fatalf("expected one emitted request, got %d", len(emitted))
	}
	if !emitted[0].RequestOnly {
		t.Fatal("RescanRequest.RequestOnly must be true")
	}

	// Structural guarantee: the request type exposes no score/band/rubric field,
	// so the trigger path cannot write a score change directly.
	forbidden := []string{"score", "band", "rubric", "subscore", "are_total", "confidence"}
	requestType := reflect.TypeOf(RescanRequest{})
	for i := 0; i < requestType.NumField(); i++ {
		name := strings.ToLower(requestType.Field(i).Name)
		for _, bad := range forbidden {
			if strings.Contains(name, bad) {
				t.Fatalf("RescanRequest must not carry a scoring field, found %q", requestType.Field(i).Name)
			}
		}
	}
}

// Requests are deterministic in the triggering evidence: identical rows yield the
// same RequestID so a spine consumer can dedupe replays.
func TestRequestIDIsDeterministicForReplaySafety(t *testing.T) {
	first := NewDetector(DefaultConfig(), &RecordingSpine{})
	first.Observe(row("gpt-4o", nil, nil, ""))
	r1, _ := first.Observe(row("claude-opus-4-8", nil, nil, ""))

	second := NewDetector(DefaultConfig(), &RecordingSpine{})
	second.Observe(row("gpt-4o", nil, nil, ""))
	r2, _ := second.Observe(row("claude-opus-4-8", nil, nil, ""))

	if r1[0].RequestID != r2[0].RequestID {
		t.Fatalf("identical triggering evidence must yield the same RequestID: %s vs %s", r1[0].RequestID, r2[0].RequestID)
	}
}

// Baselines are per (tenant, agent, deployment): one tenant's model does not
// trigger a rescan on another tenant's stream.
func TestStreamsAreTenantIsolated(t *testing.T) {
	detector := NewDetector(DefaultConfig(), &RecordingSpine{})
	a := EvidenceRow{TenantID: "t1", AgentID: "a1", DeploymentID: "prod", ModelRef: "gpt-4o"}
	b := EvidenceRow{TenantID: "t2", AgentID: "a1", DeploymentID: "prod", ModelRef: "claude-opus-4-8"}

	detector.Observe(a)
	if reqs, _ := detector.Observe(b); len(reqs) != 0 {
		t.Fatalf("a different tenant's first row must seed its own baseline, not trigger: %+v", reqs)
	}
}

func TestObserveRejectsUnattributableRows(t *testing.T) {
	detector := NewDetector(DefaultConfig(), &RecordingSpine{})
	if _, err := detector.Observe(EvidenceRow{AgentID: "a1", DeploymentID: "prod", ModelRef: "x"}); err == nil {
		t.Fatal("expected an error for a row missing tenant identity")
	}
}

// The projector maps Evidence Profile v1 backbond.evidence.* attributes onto an
// EvidenceRow, proving detection is evidence-derived and not a side channel.
func TestProjectRowFromEvidenceProfileAttributes(t *testing.T) {
	attributes := map[string]any{
		"backbond.evidence.tenant_id":      "t1",
		"backbond.evidence.agent_id":       "a1",
		"backbond.evidence.deployment_id":  "prod",
		"backbond.evidence.model_ref":      "claude-opus-4-8",
		"backbond.evidence.outcome_status": "escalated",
		"backbond.evidence.content_hash":   "sha256:" + strings.Repeat("b", 64),
		"backbond.evidence.batch_seq":      float64(42),
		"backbond.evidence.authority_context": map[string]any{
			"tools":         []any{"send_email", "wire_transfer"},
			"permissions":   []any{"read"},
			"approval_path": "manager",
			"human_in_loop": true,
		},
		"event_time": "2026-07-03T00:00:00Z",
	}
	got, ok := ProjectRow(attributes)
	if !ok {
		t.Fatal("expected a projectable row")
	}
	if got.ModelRef != "claude-opus-4-8" || got.OutcomeStatus != "escalated" || got.BatchSeq != 42 {
		t.Fatalf("unexpected projection: %+v", got)
	}
	if !reflect.DeepEqual(got.AuthorityContext.Tools, []string{"send_email", "wire_transfer"}) {
		t.Fatalf("unexpected authority tools: %+v", got.AuthorityContext.Tools)
	}
	if !got.AuthorityContext.HumanInLoop || got.AuthorityContext.ApprovalPath != "manager" {
		t.Fatalf("unexpected authority context: %+v", got.AuthorityContext)
	}

	// End to end: a projected row drives detection.
	detector := NewDetector(DefaultConfig(), &RecordingSpine{})
	detector.Observe(EvidenceRow{TenantID: "t1", AgentID: "a1", DeploymentID: "prod", ModelRef: "gpt-4o"})
	reqs, _ := detector.Observe(got)
	if len(reqs) == 0 || reqs[0].TriggerClass != ModelRefChange {
		t.Fatalf("projected row should drive a model_ref_change, got %+v", reqs)
	}
}

func TestProjectRowRejectsMissingIdentity(t *testing.T) {
	if _, ok := ProjectRow(map[string]any{"backbond.evidence.agent_id": "a1"}); ok {
		t.Fatal("row missing tenant_id/deployment_id must not project")
	}
}

func provenance() *ToolchainProvenance {
	return &ToolchainProvenance{
		ArtifactDigest:    "sha256:artifact-a",
		DependencyDigest:  "sha256:lock-a",
		MCPManifestDigest: "sha256:mcp-a",
		WorkflowIdentity:  "github:deploy.yml@refs/heads/main",
		InheritedCredentials: []CredentialProvenance{{
			Class: "cloud_role", Source: "workload_identity", Scopes: []string{"objects:read"},
		}},
		DeclaredDestinations:  []string{"api.vendor.example:443"},
		ContactedDestinations: []string{"api.vendor.example:443"},
	}
}

func provenanceRow(value *ToolchainProvenance) EvidenceRow {
	result := row("", nil, nil, "")
	result.ToolchainProvenance = value
	return result
}

func TestIdenticalToolchainProvenanceDoesNotTrigger(t *testing.T) {
	detector := NewDetector(DefaultConfig(), &RecordingSpine{})
	if reqs, err := detector.Observe(provenanceRow(provenance())); err != nil || len(reqs) != 0 {
		t.Fatalf("baseline must not trigger: reqs=%+v err=%v", reqs, err)
	}
	if reqs, err := detector.Observe(provenanceRow(provenance())); err != nil || len(reqs) != 0 {
		t.Fatalf("identical provenance must not trigger: reqs=%+v err=%v", reqs, err)
	}
}

func TestEachToolchainDigestMutationEmitsOneDeterministicRequest(t *testing.T) {
	mutations := map[string]func(*ToolchainProvenance){
		"artifact":   func(p *ToolchainProvenance) { p.ArtifactDigest = "sha256:artifact-b" },
		"dependency": func(p *ToolchainProvenance) { p.DependencyDigest = "sha256:lock-b" },
		"mcp":        func(p *ToolchainProvenance) { p.MCPManifestDigest = "sha256:mcp-b" },
		"workflow":   func(p *ToolchainProvenance) { p.WorkflowIdentity = "github:deploy.yml@refs/tags/v2" },
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			run := func() RescanRequest {
				detector := NewDetector(DefaultConfig(), &RecordingSpine{})
				detector.Observe(provenanceRow(provenance()))
				changed := provenance()
				mutate(changed)
				reqs, err := detector.Observe(provenanceRow(changed))
				if err != nil || len(reqs) != 1 || reqs[0].TriggerClass != ToolchainProvenanceChange {
					t.Fatalf("expected one toolchain request: reqs=%+v err=%v", reqs, err)
				}
				return reqs[0]
			}
			if first, second := run(), run(); first.RequestID != second.RequestID {
				t.Fatalf("request id is not deterministic: %s != %s", first.RequestID, second.RequestID)
			}
		})
	}
}

func TestInheritedCredentialScopeExpansionTriggersWithoutCredentialValue(t *testing.T) {
	detector := NewDetector(DefaultConfig(), &RecordingSpine{})
	detector.Observe(provenanceRow(provenance()))
	changed := provenance()
	changed.InheritedCredentials[0].Scopes = []string{"objects:read", "objects:write"}
	reqs, err := detector.Observe(provenanceRow(changed))
	if err != nil || len(reqs) != 1 {
		t.Fatalf("expected one request: reqs=%+v err=%v", reqs, err)
	}
	serialized := reqs[0].Reason
	for key, value := range reqs[0].Details {
		serialized += key + value
	}
	if strings.Contains(serialized, "credential_value") || strings.Contains(serialized, "secret") {
		t.Fatalf("request persisted credential material: %s", serialized)
	}
}

func TestNewUndeclaredDestinationTriggersAndIsRecorded(t *testing.T) {
	detector := NewDetector(DefaultConfig(), &RecordingSpine{})
	detector.Observe(provenanceRow(provenance()))
	changed := provenance()
	changed.ContactedDestinations = append(changed.ContactedDestinations, "NEW.EXAMPLE:443")
	reqs, err := detector.Observe(provenanceRow(changed))
	if err != nil || len(reqs) != 1 {
		t.Fatalf("expected one request: reqs=%+v err=%v", reqs, err)
	}
	if reqs[0].Details["new_destinations"] != "new.example:443" {
		t.Fatalf("expected normalized destination evidence, got %+v", reqs[0].Details)
	}
}

func TestIncompleteToolchainProvenanceFailsClosed(t *testing.T) {
	incomplete := provenance()
	incomplete.MCPManifestDigest = ""
	detector := NewDetector(DefaultConfig(), &RecordingSpine{})
	if _, err := detector.Observe(provenanceRow(incomplete)); err == nil {
		t.Fatal("incomplete explicit provenance must fail closed")
	}
}

func TestProjectToolchainProvenanceFromEvidenceProfileAttributes(t *testing.T) {
	attributes := map[string]any{
		"backbond.evidence.tenant_id":     "t1",
		"backbond.evidence.agent_id":      "a1",
		"backbond.evidence.deployment_id": "prod",
		"backbond.evidence.toolchain_provenance": map[string]any{
			"artifact_digest":     "sha256:artifact-a",
			"dependency_digest":   "sha256:lock-a",
			"mcp_manifest_digest": "sha256:mcp-a",
			"workflow_identity":   "github:deploy.yml@refs/heads/main",
			"inherited_credentials": []any{map[string]any{
				"class": "cloud_role", "source": "workload_identity",
				"scopes": []any{"objects:read"},
			}},
			"declared_destinations":  []any{"api.vendor.example:443"},
			"contacted_destinations": []any{"api.vendor.example:443"},
		},
	}
	got, ok := ProjectRow(attributes)
	if !ok || got.ToolchainProvenance == nil {
		t.Fatalf("expected projected provenance, got %+v", got)
	}
	if got.ToolchainProvenance.InheritedCredentials[0].Scopes[0] != "objects:read" {
		t.Fatalf("unexpected credential projection: %+v", got.ToolchainProvenance)
	}
}
