package source

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"backbond/are-collector/internal/schemav1"
)

const (
	DatadogMappingVersion      = "datadog-logs-v2-map-v0.1"
	DatadogSpansMappingVersion = "datadog-llmobs-spans-v0.1"
)

type HTTPDoer interface {
	Do(*http.Request) (*http.Response, error)
}

type DatadogConfig struct {
	Site, APIKey, ApplicationKey, Query string
	Dataset                             string // "logs" (default) or "spans"
	Client                              HTTPDoer
}

type DatadogSourceDriver struct {
	endpoint                          *url.URL
	apiKey, appKey, query, checkpoint string
	client                            HTTPDoer
	dataset                           string
}

// datadogCursor is the durable high-water mark persisted across collection windows.
// AfterTS/AfterIDs are the durable part: the RFC3339 timestamp of the latest event seen
// and the IDs of all events at that timestamp (for boundary deduplication).
// PageCursor and WindowTo are transient: they track mid-window pagination state and are
// cleared once all pages of a window have been drained.
type datadogCursor struct {
	AfterTS    string   `json:"after_ts,omitempty"`
	AfterIDs   []string `json:"after_ids,omitempty"`
	PageCursor string   `json:"page,omitempty"`
	WindowTo   string   `json:"window_to,omitempty"`
}

func encodeDatadogCursor(c datadogCursor) string {
	b, _ := json.Marshal(c)
	return base64.RawURLEncoding.EncodeToString(b)
}

func decodeDatadogCursor(s string) (datadogCursor, error) {
	if s == "" {
		return datadogCursor{}, nil
	}
	b, err := base64.RawURLEncoding.DecodeString(s)
	if err != nil {
		return datadogCursor{}, fmt.Errorf("invalid datadog checkpoint encoding")
	}
	var c datadogCursor
	if err := json.Unmarshal(b, &c); err != nil {
		return datadogCursor{}, fmt.Errorf("invalid datadog checkpoint: %w", err)
	}
	return c, nil
}

// normalizeDatadogSite accepts a bare Datadog site hostname (e.g. "datadoghq.com") and
// derives the API origin "https://api.<host>", or accepts a full https origin directly
// (e.g. "https://api.datadoghq.com"). The returned URL has no path component.
func normalizeDatadogSite(site string) (*url.URL, error) {
	s := strings.TrimSpace(site)
	if !strings.Contains(s, "://") {
		// Bare hostname: Datadog's DATADOG_SITE convention (e.g. "datadoghq.com")
		s = "https://api." + s
	}
	u, err := url.Parse(s)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return nil, fmt.Errorf("datadog site must be an https origin or bare hostname (e.g. datadoghq.com)")
	}
	if u.Path != "" && u.Path != "/" {
		return nil, fmt.Errorf("datadog site must not contain a path")
	}
	u.Path = ""
	return u, nil
}

func NewDatadogSourceDriver(config DatadogConfig) (*DatadogSourceDriver, error) {
	u, err := normalizeDatadogSite(config.Site)
	if err != nil {
		return nil, err
	}
	if config.APIKey == "" || config.ApplicationKey == "" {
		return nil, fmt.Errorf("datadog API and application keys are required")
	}
	ep := *u
	dataset := strings.ToLower(strings.TrimSpace(config.Dataset))
	if dataset == "" {
		dataset = "logs"
	}
	switch dataset {
	case "logs":
		ep.Path = "/api/v2/logs/events/search"
	case "spans":
		ep.Path = "/api/v2/llm-obs/v1/spans/events/search"
	default:
		return nil, fmt.Errorf("datadog dataset must be logs or spans")
	}
	client := config.Client
	if client == nil {
		client = &http.Client{Timeout: 30 * time.Second, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
	}
	return &DatadogSourceDriver{endpoint: &ep, apiKey: config.APIKey, appKey: config.ApplicationKey, query: config.Query, dataset: dataset, client: client}, nil
}

func (d *DatadogSourceDriver) Descriptor() Descriptor {
	if d.dataset == "spans" {
		return Descriptor{DriverID: "datadog", DriverVersion: InterfaceVersion, SourceClass: "llm_observability_spans", SourcePin: "datadog-llmobs-spans-search-v1", EvidenceQuality: EvidenceGateway}
	}
	return Descriptor{DriverID: "datadog", DriverVersion: InterfaceVersion, SourceClass: "logs_api_v2", SourcePin: "datadog-logs-api-v2", EvidenceQuality: EvidenceGateway}
}
func (d *DatadogSourceDriver) Checkpoint(context.Context) (string, error) { return d.checkpoint, nil }
func (d *DatadogSourceDriver) Commit(_ context.Context, checkpoint string) error {
	d.checkpoint = checkpoint
	return nil
}

func (d *DatadogSourceDriver) Acquire(ctx context.Context, after string, max int) ([]RawEnvelope, string, error) {
	if max <= 0 {
		max = 100
	}
	cursor, err := decodeDatadogCursor(after)
	if err != nil {
		return nil, after, &Error{Kind: FailureSchema, Err: err}
	}

	// Determine the window time range.
	// from: the high-water timestamp (or now-15m on first run)
	// to:   now for a new window, or the fixed WindowTo if mid-window pagination
	from := "now-15m"
	if cursor.AfterTS != "" {
		from = cursor.AfterTS
	}
	to := time.Now().UTC().Format(time.RFC3339)
	if cursor.PageCursor != "" && cursor.WindowTo != "" {
		to = cursor.WindowTo
	}

	attrs := map[string]any{
		"filter": map[string]any{"query": d.query, "from": from, "to": to},
		"page":   map[string]any{"limit": max},
	}
	if cursor.PageCursor != "" {
		attrs["page"].(map[string]any)["cursor"] = cursor.PageCursor
	}
	var body map[string]any
	if d.dataset == "spans" {
		body = map[string]any{"data": map[string]any{"type": "spans", "attributes": attrs}}
	} else {
		attrs["sort"] = "timestamp"
		body = attrs
	}
	raw, _ := json.Marshal(body)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, d.endpoint.String(), bytes.NewReader(raw))
	if err != nil {
		return nil, after, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	req.Header.Set("DD-API-KEY", d.apiKey)
	req.Header.Set("DD-APPLICATION-KEY", d.appKey)
	resp, err := d.client.Do(req)
	if err != nil {
		return nil, after, err
	}
	defer resp.Body.Close()
	if resp.Request != nil && !sameOrigin(resp.Request.URL, d.endpoint) {
		return nil, after, &Error{Kind: FailureAuth, Err: fmt.Errorf("datadog redirect or host change rejected")}
	}
	if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden {
		return nil, after, &Error{Kind: FailureAuth, Err: fmt.Errorf("datadog authorization denied (%d)", resp.StatusCode)}
	}
	if resp.StatusCode == http.StatusTooManyRequests {
		return nil, after, &Error{Kind: FailureRateLimit, Err: fmt.Errorf("datadog rate limited")}
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
		return nil, after, &Error{Kind: FailureTransient, Err: fmt.Errorf("datadog request failed (%d)", resp.StatusCode)}
	}
	var payload struct {
		Data []struct {
			ID, Type   string
			Attributes map[string]any `json:"attributes"`
		} `json:"data"`
		Meta struct {
			Page struct {
				After string `json:"after"`
			} `json:"page"`
		} `json:"meta"`
	}
	dec := json.NewDecoder(io.LimitReader(resp.Body, 16<<20))
	dec.UseNumber()
	if err := dec.Decode(&payload); err != nil {
		return nil, after, &Error{Kind: FailureSchema, Err: fmt.Errorf("datadog response schema: %w", err)}
	}

	// Build the boundary set: events at cursor.AfterTS that we already delivered.
	priorBoundary := make(map[string]bool, len(cursor.AfterIDs))
	for _, id := range cursor.AfterIDs {
		priorBoundary[id] = true
	}

	// Track the running max timestamp across all events in this page (for advancing the hw mark).
	runMaxTS := cursor.AfterTS
	runMaxIDs := append([]string(nil), cursor.AfterIDs...)

	out := make([]RawEnvelope, 0, len(payload.Data))
	for _, item := range payload.Data {
		expectedType := "log"
		if d.dataset == "spans" {
			expectedType = "span"
		}
		if item.ID == "" || item.Type != expectedType || item.Attributes == nil {
			return nil, after, &Error{Kind: FailureSchema, Err: fmt.Errorf("datadog %s resource missing id, type, or attributes", expectedType)}
		}
		ts := datadogObservedAt(item.Attributes)
		if ts == "" {
			return nil, after, &Error{Kind: FailureSchema, Err: fmt.Errorf("datadog %s resource missing timestamp", expectedType)}
		}

		// Advance the running max using time order, not RFC3339 string order.
		// A whole-second value ends in "Z", which sorts after the fractional
		// separator in a later value from the same second (for example,
		// 12:22:44Z versus 12:22:44.32Z).
		if datadogTimestampAfter(ts, runMaxTS) {
			runMaxTS = ts
			runMaxIDs = []string{item.ID}
		} else if ts == runMaxTS && !contains(runMaxIDs, item.ID) {
			runMaxIDs = append(runMaxIDs, item.ID)
		}

		// Skip events that were already delivered at the prior high-water boundary.
		if ts == cursor.AfterTS && priorBoundary[item.ID] {
			continue
		}
		out = append(out, RawEnvelope{Offset: item.ID, Payload: item.Attributes})
	}

	nextCursor := datadogCursor{AfterTS: runMaxTS, AfterIDs: runMaxIDs}
	if p := payload.Meta.Page.After; p != "" {
		// More pages remain in this window: preserve WindowTo so the upper bound stays fixed.
		nextCursor.PageCursor = p
		nextCursor.WindowTo = to
	}
	return out, encodeDatadogCursor(nextCursor), nil
}

func (d *DatadogSourceDriver) Normalize(raw RawEnvelope) (NormalizedEvent, error) {
	ts := datadogObservedAt(raw.Payload)
	if raw.Offset == "" || ts == "" {
		return NormalizedEvent{}, fmt.Errorf("datadog %s requires id and timestamp", d.dataset)
	}
	if d.dataset == "spans" {
		attrs := map[string]any{"schema_event_type": datadogSpanEventType(raw.Payload), "datadog.coverage": "llm_observability_spans", "datadog.trajectory_coverage": true}
		for _, k := range []string{"trace_id", "span_id", "parent_id", "name", "kind", "ml_app", "agent_id", "deployment_id", "action_class", "outcome_status", "model_name", "model_version", "status", "start_timestamp", "end_timestamp"} {
			if v, ok := raw.Payload[k]; ok {
				attrs[k] = v
			}
		}
		if _, ok := attrs["kind"]; !ok {
			if v, ok := raw.Payload["span_kind"]; ok {
				attrs["kind"] = v
			}
		}
		if _, ok := attrs["start_timestamp"]; !ok {
			attrs["start_timestamp"] = ts
		}
		if evaluation, ok := raw.Payload["evaluation"].(map[string]any); ok {
			safe := map[string]any{}
			for label, value := range evaluation {
				if m, ok := value.(map[string]any); ok {
					safe[label] = safeDatadogEvaluation(label, m)
				}
			}
			attrs["evaluation"] = safe
		}
		return NormalizedEvent{SourceEventID: raw.Offset, ObservedAt: ts, AgentID: stringValue(attrs["agent_id"]), DeploymentID: stringValue(attrs["deployment_id"]), OperationName: stringValue(attrs["name"]), ActionClass: stringValue(attrs["action_class"]), OutcomeStatus: stringValue(attrs["outcome_status"]), Attributes: attrs, SourceIntegrity: map[string]any{"api": "llm_observability_spans_search_v1", "coverage": "collection coverage incomplete", "sampling": "unknown"}, MappingVersion: DatadogSpansMappingVersion}, nil
	}
	attrs := map[string]any{"schema_event_type": "agent_invocation", "datadog.coverage": "logs_v2_only", "datadog.trajectory_coverage": false}
	for _, k := range []string{"service", "status", "host"} {
		if v, ok := raw.Payload[k]; ok {
			attrs[k] = v
		}
	}
	custom, _ := raw.Payload["attributes"].(map[string]any)
	for _, k := range []string{"event_type", "session_id", "agent_id", "deployment_id", "operation_name", "action_class", "outcome_status", "trace_id", "span_id"} {
		if v, ok := custom[k]; ok {
			attrs[k] = v
		}
	}
	eventType := stringValue(attrs["event_type"])
	if eventType != "" {
		attrs["schema_event_type"] = eventType
	}
	return NormalizedEvent{SourceEventID: raw.Offset, ObservedAt: ts, SessionID: stringValue(attrs["session_id"]), AgentID: stringValue(attrs["agent_id"]), DeploymentID: stringValue(attrs["deployment_id"]), OperationName: stringValue(attrs["operation_name"]), ActionClass: stringValue(attrs["action_class"]), OutcomeStatus: stringValue(attrs["outcome_status"]), Attributes: attrs, SourceIntegrity: map[string]any{"api": "logs_v2", "coverage": "collection coverage incomplete"}, MappingVersion: DatadogMappingVersion}, nil
}

func safeDatadogEvaluation(label string, value map[string]any) map[string]any {
	safe := map[string]any{}
	for _, key := range []string{"eval_metric_type", "value", "evaluator_id", "evaluator_version", "scope", "timestamp", "evaluation_time", "judgment_type", "outcome_type"} {
		if v, ok := value[key]; ok {
			safe[key] = v
		}
	}
	kind := "automated_judgment"
	if strings.Contains(strings.ToLower(label), "confirmed") || strings.EqualFold(stringValue(value["judgment_type"]), "confirmed_outcome") || strings.EqualFold(stringValue(value["outcome_type"]), "confirmed") {
		kind = "confirmed_outcome"
	}
	safe["judgment_kind"] = kind
	return safe
}

func datadogSpanEventType(payload map[string]any) string {
	kind := stringValue(payload["kind"])
	if kind == "" {
		kind = stringValue(payload["span_kind"])
	}
	if kind == "tool" {
		return string(schemav1.EventToolCall)
	}
	return string(schemav1.EventAgentInvocation)
}

func sameOrigin(a, b *url.URL) bool {
	return a != nil && b != nil && a.Scheme == b.Scheme && a.Host == b.Host
}

// datadogObservedAt accepts both the preview export's RFC3339 timestamp fields
// and the current Agent span shape, which reports start_ns as a JSON number.
// UseNumber above is required so a nanosecond epoch does not round through a
// float64 before conversion.
func datadogObservedAt(payload map[string]any) string {
	for _, key := range []string{"timestamp", "start_timestamp"} {
		if value := stringValue(payload[key]); value != "" {
			return value
		}
	}
	if value, ok := payload["start_ns"]; ok {
		var ns int64
		switch number := value.(type) {
		case json.Number:
			ns, _ = number.Int64()
		case int64:
			ns = number
		case int:
			ns = int64(number)
		case float64:
			if number == float64(int64(number)) {
				ns = int64(number)
			}
		}
		if ns > 0 {
			return time.Unix(0, ns).UTC().Format(time.RFC3339Nano)
		}
	}
	return ""
}

func datadogTimestampAfter(candidate, current string) bool {
	if current == "" {
		return candidate != ""
	}
	candidateTime, candidateErr := time.Parse(time.RFC3339Nano, candidate)
	currentTime, currentErr := time.Parse(time.RFC3339Nano, current)
	if candidateErr != nil || currentErr != nil {
		return candidate > current
	}
	return candidateTime.After(currentTime)
}

func contains(ss []string, s string) bool {
	for _, v := range ss {
		if v == s {
			return true
		}
	}
	return false
}
