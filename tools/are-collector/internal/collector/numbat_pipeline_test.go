package collector

import (
	"context"
	"strings"
	"testing"

	"backbond/are-collector/internal/egress"
	"backbond/are-collector/internal/schemav1"
	"backbond/are-collector/internal/source"
)

const numbatFixture = `
{"event_type":"tool.call","event_id":"call-1","observed_at":"2026-07-30T20:00:00Z","session_id":"s1","agent_id":"a1","deployment_id":"d1","call_id":"c1","parameters":{"email":"private@example.com","token":"sk-abcdefghijklmnopqrstuvwxyz"}}
{"event_type":"tool.result","event_id":"result-1","observed_at":"2026-07-30T20:00:01Z","session_id":"s1","agent_id":"a1","deployment_id":"d1","call_id":"c1","content":"private@example.com"}
{"event_type":"future.kind","event_id":"future-1","observed_at":"2026-07-30T20:00:02Z","session_id":"s1","agent_id":"a1","deployment_id":"d1"}
`

func collectNumbat(t *testing.T) []schemav1.Event {
	t.Helper()
	driver, err := source.NewNumbatSourceDriver(strings.NewReader(numbatFixture))
	if err != nil {
		t.Fatal(err)
	}
	events, err := (Pipeline{}).CollectDriver(context.Background(), driver, 100)
	if err != nil {
		t.Fatal(err)
	}
	return events
}

func TestNumbatPipelineRedactsPreservesAndIsCanonical(t *testing.T) {
	first := collectNumbat(t)
	second := collectNumbat(t)
	if len(first) != 3 {
		t.Fatalf("events=%d", len(first))
	}
	raw, err := schemav1.CanonicalJSON(first)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw), "private@example.com") || strings.Contains(string(raw), "abcdefghijklmnopqrstuvwxyz") {
		t.Fatalf("sensitive content escaped: %s", raw)
	}
	a, err := egress.ComputeBatchSHA256(first)
	if err != nil {
		t.Fatal(err)
	}
	b, err := egress.ComputeBatchSHA256(second)
	if err != nil {
		t.Fatal(err)
	}
	if a != b {
		t.Fatalf("golden hashes differ: %s != %s", a, b)
	}
	if first[2].Payload["billable"] != false {
		t.Fatalf("unknown kind billed: %#v", first[2])
	}
}

func TestNumbatFailureHoldsCheckpoint(t *testing.T) {
	driver, err := source.NewNumbatSourceDriver(strings.NewReader(
		`{"event_type":"tool.call","event_id":"bad","observed_at":"not-a-time"}`,
	))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := (Pipeline{}).CollectDriver(context.Background(), driver, 10); err == nil {
		t.Fatal("expected validation failure")
	}
	checkpoint, _ := driver.Checkpoint(context.Background())
	if checkpoint != "" {
		t.Fatalf("failure advanced checkpoint to %q", checkpoint)
	}
}
