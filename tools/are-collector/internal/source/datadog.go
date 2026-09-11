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
)

const DatadogMappingVersion = "datadog-logs-v2-map-v0.1"

type HTTPDoer interface {
	Do(*http.Request) (*http.Response, error)
}

type DatadogConfig struct {
	Site, APIKey, ApplicationKey, Query string
	Client                              HTTPDoer
}

type DatadogSourceDriver struct {
	endpoint                          *url.URL
	apiKey, appKey, query, checkpoint string
	client                            HTTPDoer
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
	ep.Path = "/api/v2/logs/events/search"
	client := config.Client
	if client == nil {
		client = &http.Client{Timeout: 30 * time.Second, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
	}
	return &DatadogSourceDriver{endpoint: &ep, apiKey: config.APIKey, appKey: config.ApplicationKey, query: config.Query, client: client}, nil
}

func (d *DatadogSourceDriver) Descriptor() Descriptor {
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

	body := map[string]any{
		"filter": map[string]any{"query": d.query, "from": from, "to": to},
		"page":   map[string]any{"limit": max},
		"sort":   "timestamp",
	}
	if cursor.PageCursor != "" {
		body["page"].(map[string]any)["cursor"] = cursor.PageCursor
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
		if item.ID == "" || item.Type != "log" || item.Attributes == nil {
			return nil, after, &Error{Kind: FailureSchema, Err: fmt.Errorf("datadog log resource missing id, type, or attributes")}
		}
		ts := stringValue(item.Attributes["timestamp"])

		// Advance the running max.
		if ts > runMaxTS {
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
	ts := stringValue(raw.Payload["timestamp"])
	if raw.Offset == "" || ts == "" {
		return NormalizedEvent{}, fmt.Errorf("datadog log requires id and timestamp")
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

func sameOrigin(a, b *url.URL) bool {
	return a != nil && b != nil && a.Scheme == b.Scheme && a.Host == b.Host
}

func contains(ss []string, s string) bool {
	for _, v := range ss {
		if v == s {
			return true
		}
	}
	return false
}
