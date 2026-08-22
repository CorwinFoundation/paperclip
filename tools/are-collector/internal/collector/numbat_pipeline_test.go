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
{"schema_version":"0.2.0","event_type":"tool.call","event_id":"call-1","observed_at":"2026-07-30T20:00:00Z","session_id":"s1","agent_id":"a1","deployment_id":"d1","call_id":"c1","parameters":{"email":"private@example.com","token":"sk-abcdefghijklmnopqrstuvwxyz"}}
{"schema_version":"0.2.0","event_type":"tool.result","event_id":"result-1","observed_at":"2026-07-30T20:00:01Z","session_id":"s1","agent_id":"a1","deployment_id":"d1","call_id":"c1","content":"private@example.com"}
{"schema_version":"0.2.0","event_type":"future.kind","event_id":"future-1","observed_at":"2026-07-30T20:00:02Z","session_id":"s1","agent_id":"a1","deployment_id":"d1"}
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
		`{"schema_version":"0.2.0","event_type":"tool.call","event_id":"bad","observed_at":"not-a-time"}`,
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

func TestNumbatOfficialRecordsAreRedactedEndToEnd(t *testing.T) {
	driver, err := source.NewNumbatSourceDriver(strings.NewReader(strings.Join([]string{
		`{"schema_version":"0.2.0","record_type":"indicator","run_id":"r1","type":"domain","value":"private.example","count":1,"endpoint":{"hostname":"private-host","os":"linux","arch":"amd64","username":"private-user","uid":"private-uid"}}`,
		`{"schema_version":"0.2.0","record_type":"diagnostic","run_id":"r1","timestamp":"2026-07-31T00:00:03Z","level":"warn","message":"private-message","endpoint":{"hostname":"private-host","os":"linux","arch":"amd64","username":"private-user","uid":"private-uid"}}`,
	}, "\n")))
	if err != nil {
		t.Fatal(err)
	}
	events, err := (Pipeline{}).CollectDriver(context.Background(), driver, 10)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := schemav1.CanonicalJSON(events)
	if err != nil {
		t.Fatal(err)
	}
	for _, secret := range []string{"private.example", "private-message", "private-host", "private-user", "private-uid"} {
		if strings.Contains(string(raw), secret) {
			t.Fatalf("official record leaked %q: %s", secret, raw)
		}
	}
}
