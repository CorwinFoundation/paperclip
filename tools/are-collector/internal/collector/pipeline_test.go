package collector

import (
	"context"
	"strings"
	"testing"

	"backbond/are-collector/internal/adapter"
	"backbond/are-collector/internal/redaction"
)

func TestPipelineFiltersAndRedactsSchemaEvents(t *testing.T) {
	input := strings.NewReader(`
{"event_type":"tool_call","event_id":"evt-1","logged_at":"2026-06-16T18:00:00Z","payload":{"tool":"payments","args":{"email":"jane@example.com","token":"sk-secretsecretsecretsecret","debug":"remove me"}}}
{"event_type":"debug_log","event_id":"evt-2","logged_at":"2026-06-16T18:00:01Z","payload":{"message":"drop"}}
`)
	sink := adapter.NewDatadogJSONLines(input)
	pipeline := Pipeline{
		Adapter: sink,
		Policy: redaction.Policy{
			"args.email": redaction.RedactOnReceipt,
			"args.token": redaction.Hash,
			"args.debug": redaction.Drop,
		},
	}
	events, err := pipeline.Collect(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(events) != 1 {
		t.Fatalf("expected one schema event, got %d", len(events))
	}
	event := events[0]
	args := event.Payload["args"].(map[string]any)
	if args["email"] != "[REDACTED:email]" {
		t.Fatalf("email was not redacted: %#v", args["email"])
	}
	if !strings.HasPrefix(args["token"].(string), "sha256:") {
		t.Fatalf("token was not hashed: %#v", args["token"])
	}
	if _, ok := args["debug"]; ok {
		t.Fatal("drop field was emitted")
	}
	joined := strings.Join(event.RedactionApplied, ",")
	for _, path := range []string{"args.debug", "args.email", "args.token"} {
		if !strings.Contains(joined, path) {
			t.Fatalf("missing redaction path %s in %#v", path, event.RedactionApplied)
		}
	}
}

func TestAdapterFactoryCoversMVPConnectors(t *testing.T) {
	for _, source := range []string{"datadog", "splunk", "cloudwatch", "otel"} {
		sink, err := adapter.AdapterFromFixture(source, strings.NewReader(`{"event_type":"error","logged_at":"2026-06-16T18:00:00Z","payload":{"message":"x"}}`))
		if err != nil {
			t.Fatalf("%s: %v", source, err)
		}
		if string(sink.SourceKind()) != source {
			t.Fatalf("source kind mismatch: got %s want %s", sink.SourceKind(), source)
		}
	}
}

func TestPipelineReadsMultipleTopLevelJSONRecords(t *testing.T) {
	input := strings.NewReader(`
{"event_type":"error","event_id":"evt-1","logged_at":"2026-06-16T18:00:00Z","payload":{"message":"first"}}
{"event_type":"error","event_id":"evt-2","logged_at":"2026-06-16T18:00:01Z","payload":{"message":"second"}}
`)
	pipeline := Pipeline{Adapter: adapter.NewSplunkJSONLines(input)}
	events, err := pipeline.Collect(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(events) != 2 {
		t.Fatalf("expected two events, got %d", len(events))
	}
}

func TestPipelineDropsMalformedSchemaFamilyEvent(t *testing.T) {
	input := strings.NewReader(`{"event_type":"tool_call","event_id":"evt-bad","logged_at":"not-rfc3339","payload":{"tool":"payments"}}`)
	pipeline := Pipeline{Adapter: adapter.NewCloudWatchJSONLines(input)}
	events, err := pipeline.Collect(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(events) != 0 {
		t.Fatalf("malformed schema-family event was emitted: %#v", events)
	}
}
