package source

import (
	"context"
	"strings"
	"testing"
)

func TestNumbatMappingClosedUnknownAndPairBilling(t *testing.T) {
	input := strings.NewReader(`
{"event_type":"tool.call","event_id":"call-1","observed_at":"2026-07-30T20:00:00Z","call_id":"c1","parameters":{"token":"secret"}}
{"event_type":"tool.result","event_id":"result-1","observed_at":"2026-07-30T20:00:01Z","call_id":"c1","content":"secret"}
{"event_type":"future.kind","event_id":"future-1","observed_at":"2026-07-30T20:00:02Z"}
`)
	driver, err := NewNumbatSourceDriver(input)
	if err != nil {
		t.Fatal(err)
	}
	records, next, err := driver.Acquire(context.Background(), "", 10)
	if err != nil {
		t.Fatal(err)
	}
	billable := 0
	for index, record := range records {
		event, err := driver.Normalize(record)
		if err != nil {
			t.Fatal(err)
		}
		if event.Billable {
			billable++
		}
		if index == 2 && (event.Billable || event.Attributes["numbat.known_kind"] != false) {
			t.Fatalf("unknown kind must be preserved observability-only: %#v", event)
		}
	}
	if billable != 1 {
		t.Fatalf("call/result pair billed %d times", billable)
	}
	if err := driver.Commit(context.Background(), next); err != nil {
		t.Fatal(err)
	}
	checkpoint, _ := driver.Checkpoint(context.Background())
	if checkpoint != "3" {
		t.Fatalf("checkpoint=%q", checkpoint)
	}
}
