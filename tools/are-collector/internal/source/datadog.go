package source

import (
	"bytes"
	"context"
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

func NewDatadogSourceDriver(config DatadogConfig) (*DatadogSourceDriver, error) {
	u, err := url.Parse(strings.TrimSpace(config.Site))
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return nil, fmt.Errorf("datadog site must be an https origin")
	}
	if u.Path != "" && u.Path != "/" {
		return nil, fmt.Errorf("datadog site must not contain a path")
	}
	if config.APIKey == "" || config.ApplicationKey == "" {
		return nil, fmt.Errorf("datadog API and application keys are required")
	}
	u.Path = "/api/v2/logs/events/search"
	client := config.Client
	if client == nil {
		client = &http.Client{Timeout: 30 * time.Second, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
	}
	return &DatadogSourceDriver{endpoint: u, apiKey: config.APIKey, appKey: config.ApplicationKey, query: config.Query, client: client}, nil
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
	body := map[string]any{"filter": map[string]any{"query": d.query, "from": "now-15m", "to": "now"}, "page": map[string]any{"limit": max}, "sort": "timestamp"}
	if after != "" {
		body["page"].(map[string]any)["cursor"] = after
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
	out := make([]RawEnvelope, 0, len(payload.Data))
	for _, item := range payload.Data {
		if item.ID == "" || item.Type != "log" || item.Attributes == nil {
			return nil, after, &Error{Kind: FailureSchema, Err: fmt.Errorf("datadog log resource missing id, type, or attributes")}
		}
		out = append(out, RawEnvelope{Offset: item.ID, Payload: item.Attributes})
	}
	next := payload.Meta.Page.After
	if next == "" {
		next = after
	}
	return out, next, nil
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
