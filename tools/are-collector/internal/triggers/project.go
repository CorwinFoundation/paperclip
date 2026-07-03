package triggers

import "backbond/are-collector/internal/schemav1"

// Evidence Profile v1 attribute keys (contracts/evidence-profile-v1.md §4). The
// projector reads trigger inputs from these keys only, so triggers are derived
// from runtime evidence rows and never from a lossy side channel.
const (
	attrTenantID         = "backbond.evidence.tenant_id"
	attrAgentID          = "backbond.evidence.agent_id"
	attrDeploymentID     = "backbond.evidence.deployment_id"
	attrModelRef         = "backbond.evidence.model_ref"
	attrAuthorityContext = "backbond.evidence.authority_context"
	attrOutcomeStatus    = "backbond.evidence.outcome_status"
	attrContentHash      = "backbond.evidence.content_hash"
	attrBatchSeq         = "backbond.evidence.batch_seq"
	attrEventTime        = "event_time"
)

// ProjectRow builds an EvidenceRow from an Evidence Profile v1 attribute bag (the
// backbond.evidence.* attribute set of a consequential event). It returns false
// when the required identity spine (tenant_id, agent_id, deployment_id) is absent,
// so an unattributable row can never seed a baseline or request a rescan.
func ProjectRow(attributes map[string]any) (EvidenceRow, bool) {
	tenant := stringAttr(attributes, attrTenantID)
	agent := stringAttr(attributes, attrAgentID)
	deployment := stringAttr(attributes, attrDeploymentID)
	if tenant == "" || agent == "" || deployment == "" {
		return EvidenceRow{}, false
	}
	row := EvidenceRow{
		TenantID:         tenant,
		AgentID:          agent,
		DeploymentID:     deployment,
		EventTime:        stringAttr(attributes, attrEventTime),
		ModelRef:         stringAttr(attributes, attrModelRef),
		OutcomeStatus:    stringAttr(attributes, attrOutcomeStatus),
		ContentHash:      stringAttr(attributes, attrContentHash),
		BatchSeq:         intAttr(attributes, attrBatchSeq),
		AuthorityContext: projectAuthority(attributes[attrAuthorityContext]),
	}
	return row, true
}

// ProjectEvent projects a collector schemav1.Event whose payload carries the
// Evidence Profile v1 attribute bag.
func ProjectEvent(event schemav1.Event) (EvidenceRow, bool) {
	return ProjectRow(event.Payload)
}

func projectAuthority(value any) AuthorityContext {
	object, ok := value.(map[string]any)
	if !ok {
		return AuthorityContext{}
	}
	return AuthorityContext{
		Tools:        stringSlice(object["tools"]),
		Permissions:  stringSlice(object["permissions"]),
		ApprovalPath: asString(object["approval_path"]),
		HumanInLoop:  asBool(object["human_in_loop"]),
	}
}

func stringAttr(attributes map[string]any, key string) string {
	if attributes == nil {
		return ""
	}
	return asString(attributes[key])
}

func intAttr(attributes map[string]any, key string) int {
	if attributes == nil {
		return 0
	}
	switch typed := attributes[key].(type) {
	case int:
		return typed
	case int64:
		return int(typed)
	case float64:
		return int(typed)
	default:
		return 0
	}
}

func asString(value any) string {
	if text, ok := value.(string); ok {
		return text
	}
	return ""
}

func asBool(value any) bool {
	if flag, ok := value.(bool); ok {
		return flag
	}
	return false
}

func stringSlice(value any) []string {
	switch typed := value.(type) {
	case []string:
		return typed
	case []any:
		out := make([]string, 0, len(typed))
		for _, item := range typed {
			if text, ok := item.(string); ok {
				out = append(out, text)
			}
		}
		return out
	default:
		return nil
	}
}
