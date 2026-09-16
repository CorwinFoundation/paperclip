package collector

import (
	"context"
	"crypto/ed25519"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"

	"backbond/are-collector/internal/egress"
	"backbond/are-collector/internal/source"
)

type datadogSpanRoundTrip func(*http.Request) (*http.Response, error)

func (f datadogSpanRoundTrip) Do(r *http.Request) (*http.Response, error) { return f(r) }

func TestDatadogSpansReachSignedBatchWithoutScorePath(t *testing.T) {
	driver, err := source.NewDatadogSourceDriver(source.DatadogConfig{
		Site: "datadoghq.com", APIKey: "api", ApplicationKey: "app", Dataset: "spans",
		Client: datadogSpanRoundTrip(func(r *http.Request) (*http.Response, error) {
			if r.URL.Path != "/api/v2/llm-obs/v1/spans/events/search" {
				t.Fatalf("path=%s", r.URL.Path)
			}
			return &http.Response{
				StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader(`{"data":[{"id":"span-tool-1","type":"span","attributes":{"start_ns":1789556400000000000,"trace_id":"trace-1","span_id":"span-tool-1","parent_id":"span-agent-1","name":"lookup_order_status","span_kind":"tool","agent_id":"agent-1","deployment_id":"deploy-1","evaluation":{"tool_action_evidenced":{"eval_metric_type":"score","value":1}}}}],"meta":{}}`)),
				Request: r,
			}, nil
		}),
	})
	if err != nil {
		t.Fatal(err)
	}
	events, err := (Pipeline{}).CollectDriver(context.Background(), driver, 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(events) != 1 || string(events[0].EventType) != "tool_call" {
		t.Fatalf("events=%+v", events)
	}
	if events[0].Payload["agent_id"] != "agent-1" || events[0].Payload["deployment_id"] != "deploy-1" {
		t.Fatalf("identity payload=%+v", events[0].Payload)
	}
	if _, ok := events[0].Payload["score"]; ok {
		t.Fatal("collector emitted a score-bearing top-level field")
	}
	_, privateKey, err := ed25519.GenerateKey(strings.NewReader(strings.Repeat("d", 64)))
	if err != nil {
		t.Fatal(err)
	}
	signed, err := egress.NewSignedBatch("controlled-non-customer", "agent-1", "batch-1", "key-1", time.Date(2026, 9, 16, 11, 0, 0, 0, time.UTC), events, privateKey)
	if err != nil {
		t.Fatal(err)
	}
	if signed.EventCount != 1 || signed.BatchSHA256 == "" {
		t.Fatalf("signed=%+v", signed)
	}
}
