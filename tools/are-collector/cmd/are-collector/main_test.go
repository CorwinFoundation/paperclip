package main

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
)

func TestCLIEmitsSignedBatch(t *testing.T) {
	input := strings.NewReader(`{"event_type":"tool_call","event_id":"evt-1","logged_at":"2026-06-16T18:00:00Z","payload":{"args":{"email":"jane@example.com"}}}`)
	out := bytes.Buffer{}
	err := run(
		[]string{
			"-source", "datadog",
			"-customer-id", "customer-1",
			"-agent-id", "agent-1",
			"-batch-id", "batch-1",
			"-policy-json", `{"args.email":"redact-on-receipt"}`,
		},
		input,
		&out,
	)
	if err != nil {
		t.Fatal(err)
	}
	var batch map[string]any
	if err := json.Unmarshal(out.Bytes(), &batch); err != nil {
		t.Fatal(err)
	}
	if batch["schema_version"] != "log-event-schema-v1.0" {
		t.Fatalf("schema_version mismatch: %#v", batch["schema_version"])
	}
	if batch["jwt"] == "" {
		t.Fatal("missing jwt")
	}
}
