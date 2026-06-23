package main

import (
	"bytes"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"strings"
	"testing"
)

func testPrivateKeyB64(t *testing.T) string {
	t.Helper()
	_, privateKey, err := ed25519.GenerateKey(strings.NewReader(strings.Repeat("a", 64)))
	if err != nil {
		t.Fatal(err)
	}
	return base64.StdEncoding.EncodeToString(privateKey)
}

func TestCLIEmitsSignedBatch(t *testing.T) {
	input := strings.NewReader(`{"event_type":"tool_call","event_id":"evt-1","logged_at":"2026-06-16T18:00:00Z","payload":{"args":{"email":"jane@example.com"}}}`)
	out := bytes.Buffer{}
	err := run(
		[]string{
			"-mode", "fixture-smoke",
			"-source", "datadog",
			"-customer-id", "customer-1",
			"-agent-id", "agent-1",
			"-batch-id", "batch-1",
			"-key-id", "bind-key-1",
			"-ed25519-private-key-b64", testPrivateKeyB64(t),
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

func TestCLIRejectsUnwiredRealMode(t *testing.T) {
	err := run(
		[]string{
			"-mode", "datadog",
			"-source", "datadog",
			"-customer-id", "customer-1",
			"-agent-id", "agent-1",
			"-batch-id", "batch-1",
			"-key-id", "bind-key-1",
			"-ed25519-private-key-b64", testPrivateKeyB64(t),
		},
		strings.NewReader(`{}`),
		&bytes.Buffer{},
	)
	if err == nil || !strings.Contains(err.Error(), "only fixture-smoke is enabled") {
		t.Fatalf("expected unwired mode error, got %v", err)
	}
}

func TestCLIRejectsMissingPrivateKey(t *testing.T) {
	err := run(
		[]string{
			"-mode", "fixture-smoke",
			"-source", "datadog",
			"-customer-id", "customer-1",
			"-agent-id", "agent-1",
			"-batch-id", "batch-1",
			"-key-id", "bind-key-1",
		},
		strings.NewReader(`{}`),
		&bytes.Buffer{},
	)
	if err == nil || !strings.Contains(err.Error(), "ed25519-private-key-b64 is required") {
		t.Fatalf("expected private key error, got %v", err)
	}
}
