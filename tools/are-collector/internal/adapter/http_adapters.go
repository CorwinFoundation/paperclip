package adapter

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

	"backbond/are-collector/internal/schemav1"
)

type DatadogConfig struct {
	Site           string
	APIKey         string
	ApplicationKey string
	Query          string
	From           time.Time
	To             time.Time
	Limit          int
	Client         HTTPDoer
}

type SplunkConfig struct {
	BaseURL string
	Token   string
	Search  string
	Client  HTTPDoer
}

type CloudWatchConfig struct {
	Endpoint        string
	Authorization   string
	LogGroupName    string
	FilterPattern   string
	StartTimeMillis int64
	EndTimeMillis   int64
	Limit           int
	Client          HTTPDoer
}

type requestAdapter struct {
	source schemav1.SourceKind
	client HTTPDoer
	build  func(context.Context) (*http.Request, error)
	decode func(io.Reader) ([]map[string]any, error)
}

func NewDatadogLogsAPI(config DatadogConfig) LogSinkAdapter {
	site := strings.TrimSpace(config.Site)
	if site == "" {
		site = "https://api.datadoghq.com"
	}
	limit := config.Limit
	if limit <= 0 {
		limit = 100
	}
	client := config.Client
	if client == nil {
		client = http.DefaultClient
	}
	return &requestAdapter{
		source: schemav1.SourceDatadog,
		client: client,
		build: func(ctx context.Context) (*http.Request, error) {
			body := map[string]any{
				"filter": map[string]any{"query": config.Query},
				"page":   map[string]any{"limit": limit},
			}
			if !config.From.IsZero() || !config.To.IsZero() {
				filter := body["filter"].(map[string]any)
				if !config.From.IsZero() {
					filter["from"] = config.From.UTC().Format(time.RFC3339)
				}
				if !config.To.IsZero() {
					filter["to"] = config.To.UTC().Format(time.RFC3339)
				}
			}
			raw, err := json.Marshal(body)
			if err != nil {
				return nil, err
			}
			req, err := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimRight(site, "/")+"/api/v2/logs/events/search", bytes.NewReader(raw))
			if err != nil {
				return nil, err
			}
			req.Header.Set("Content-Type", "application/json")
			req.Header.Set("DD-API-KEY", config.APIKey)
			req.Header.Set("DD-APPLICATION-KEY", config.ApplicationKey)
			return req, nil
		},
		decode: decodeDatadog,
	}
}

func NewSplunkRESTExport(config SplunkConfig) LogSinkAdapter {
	client := config.Client
	if client == nil {
		client = http.DefaultClient
	}
	return &requestAdapter{
		source: schemav1.SourceSplunk,
		client: client,
		build: func(ctx context.Context) (*http.Request, error) {
			base, err := url.Parse(strings.TrimRight(config.BaseURL, "/") + "/services/search/jobs/export")
			if err != nil {
				return nil, err
			}
			query := base.Query()
			query.Set("search", config.Search)
			query.Set("output_mode", "json")
			base.RawQuery = query.Encode()
			req, err := http.NewRequestWithContext(ctx, http.MethodGet, base.String(), nil)
			if err != nil {
				return nil, err
			}
			req.Header.Set("Authorization", "Splunk "+config.Token)
			return req, nil
		},
		decode: decodeSplunkExport,
	}
}

func NewCloudWatchFilterLogEvents(config CloudWatchConfig) LogSinkAdapter {
	limit := config.Limit
	if limit <= 0 {
		limit = 100
	}
	client := config.Client
	if client == nil {
		client = http.DefaultClient
	}
	return &requestAdapter{
		source: schemav1.SourceCloudWatch,
		client: client,
		build: func(ctx context.Context) (*http.Request, error) {
			body := map[string]any{
				"logGroupName":  config.LogGroupName,
				"filterPattern": config.FilterPattern,
				"limit":         limit,
			}
			if config.StartTimeMillis != 0 {
				body["startTime"] = config.StartTimeMillis
			}
			if config.EndTimeMillis != 0 {
				body["endTime"] = config.EndTimeMillis
			}
			raw, err := json.Marshal(body)
			if err != nil {
				return nil, err
			}
			req, err := http.NewRequestWithContext(ctx, http.MethodPost, config.Endpoint, bytes.NewReader(raw))
			if err != nil {
				return nil, err
			}
			req.Header.Set("Content-Type", "application/x-amz-json-1.1")
			req.Header.Set("X-Amz-Target", "Logs_20140328.FilterLogEvents")
			req.Header.Set("Authorization", config.Authorization)
			return req, nil
		},
		decode: decodeCloudWatch,
	}
}

func (adapter *requestAdapter) SourceKind() schemav1.SourceKind {
	return adapter.source
}

func (adapter *requestAdapter) Healthcheck(ctx context.Context) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	default:
	}
	if adapter.client == nil {
		return fmt.Errorf("http client is required")
	}
	if adapter.build == nil || adapter.decode == nil {
		return fmt.Errorf("request adapter is incomplete")
	}
	return nil
}

func (adapter *requestAdapter) Stream(ctx context.Context) (<-chan RawRecord, error) {
	if err := adapter.Healthcheck(ctx); err != nil {
		return nil, err
	}
	req, err := adapter.build(ctx)
	if err != nil {
		return nil, err
	}
	resp, err := adapter.client.Do(req)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		defer resp.Body.Close()
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return nil, fmt.Errorf("%s adapter request failed: %s: %s", adapter.source, resp.Status, strings.TrimSpace(string(body)))
	}
	payloads, err := adapter.decode(resp.Body)
	_ = resp.Body.Close()
	if err != nil {
		return nil, err
	}
	records := make(chan RawRecord)
	go func() {
		defer close(records)
		for _, payload := range payloads {
			select {
			case <-ctx.Done():
				return
			case records <- RawRecord{SourceKind: adapter.source, VendorPayload: payload, VendorMeta: map[string]string{"adapter": string(adapter.source)}}:
			}
		}
	}()
	return records, nil
}

func decodeDatadog(reader io.Reader) ([]map[string]any, error) {
	var response struct {
		Data []struct {
			Attributes map[string]any `json:"attributes"`
		} `json:"data"`
	}
	if err := json.NewDecoder(reader).Decode(&response); err != nil {
		return nil, err
	}
	out := make([]map[string]any, 0, len(response.Data))
	for _, item := range response.Data {
		out = append(out, payloadFromMessageOrMap(item.Attributes))
	}
	return out, nil
}

func decodeSplunkExport(reader io.Reader) ([]map[string]any, error) {
	decoder := json.NewDecoder(reader)
	out := []map[string]any{}
	for {
		var line map[string]any
		if err := decoder.Decode(&line); err != nil {
			if err == io.EOF {
				return out, nil
			}
			return nil, err
		}
		if result, ok := line["result"].(map[string]any); ok {
			out = append(out, payloadFromMessageOrMap(result))
		}
	}
}

func decodeCloudWatch(reader io.Reader) ([]map[string]any, error) {
	var response struct {
		Events []struct {
			Message   string `json:"message"`
			EventID   string `json:"eventId"`
			Timestamp int64  `json:"timestamp"`
		} `json:"events"`
	}
	if err := json.NewDecoder(reader).Decode(&response); err != nil {
		return nil, err
	}
	out := make([]map[string]any, 0, len(response.Events))
	for _, event := range response.Events {
		payload := payloadFromMessageOrMap(map[string]any{"message": event.Message})
		if _, ok := payload["event_id"]; !ok && event.EventID != "" {
			payload["event_id"] = event.EventID
		}
		if _, ok := payload["logged_at"]; !ok && event.Timestamp != 0 {
			payload["logged_at"] = time.UnixMilli(event.Timestamp).UTC().Format(time.RFC3339)
		}
		out = append(out, payload)
	}
	return out, nil
}

func payloadFromMessageOrMap(value map[string]any) map[string]any {
	for _, key := range []string{"message", "_raw"} {
		raw, ok := value[key].(string)
		if !ok {
			continue
		}
		var payload map[string]any
		if err := json.Unmarshal([]byte(raw), &payload); err == nil {
			return payload
		}
	}
	return value
}
