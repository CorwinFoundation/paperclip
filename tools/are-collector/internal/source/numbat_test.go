package source

import (
	"context"
	"strings"
	"testing"
)

func TestNumbatOfficialV020RecordFamilies(t *testing.T) {
	input := strings.NewReader(strings.Join([]string{
		`{"schema_version":"0.2.0","record_type":"event","run_id":"r1","event_id":"e1","timestamp":"2026-07-31T00:00:00Z","event_type":"tool.call","source_agent":"codex","source_type":"hook","endpoint":{"hostname":"private","os":"linux","arch":"amd64","username":"private","uid":"private","device_id":"d1"},"confidence":"high","evidence":{"artifact_type":"hook"},"command":"secret"}`,
		`{"schema_version":"0.2.0","record_type":"finding","run_id":"r1","finding_id":"f1","detected_at":"2026-07-31T00:00:01Z","rule_id":"rule-1","rule_version":"1","severity":"high","source_agent":"codex","source_type":"hook","title":"secret","endpoint":{"hostname":"private","os":"linux","arch":"amd64","username":"private","uid":"private"}}`,
		`{"schema_version":"0.2.0","record_type":"enforcement","run_id":"r1","decision_id":"enf-000000000000000000000001","timestamp":"2026-07-31T00:00:02Z","decision":"deny","mode":"enforce","reason":"enforce_rule_match","source_agent":"codex","source_type":"hook","endpoint":{"hostname":"private","os":"linux","arch":"amd64","username":"private","uid":"private"}}`,
		`{"schema_version":"0.2.0","record_type":"indicator","run_id":"r1","type":"domain","value":"private.example","count":1,"endpoint":{"hostname":"private","os":"linux","arch":"amd64","username":"private","uid":"private"}}`,
		`{"schema_version":"0.2.0","record_type":"scan_summary","run_id":"r1","status":"complete","artifacts_scanned":1,"events_emitted":1,"findings_emitted":1,"indicators_emitted":1,"diagnostics":0,"endpoint":{"hostname":"private","os":"linux","arch":"amd64","username":"private","uid":"private"}}`,
		`{"schema_version":"0.2.0","record_type":"diagnostic","run_id":"r1","timestamp":"2026-07-31T00:00:03Z","level":"warn","message":"secret","endpoint":{"hostname":"private","os":"linux","arch":"amd64","username":"private","uid":"private"}}`,
		`{"schema_version":"0.2.0","record_type":"future_family","run_id":"r1","timestamp":"2026-07-31T00:00:04Z"}`,
	}, "\n"))
	driver, err := NewNumbatSourceDriver(input)
	if err != nil {
		t.Fatal(err)
	}
	records, _, err := driver.Acquire(context.Background(), "", 20)
	if err != nil {
		t.Fatal(err)
	}
	for i, raw := range records {
		event, err := driver.Normalize(raw)
		if err != nil {
			t.Fatalf("record %d: %v", i, err)
		}
		if event.SourceEventID == "" || event.ObservedAt == "" {
			t.Fatalf("record %d lacks identity/time: %#v", i, event)
		}
		if i > 0 && event.Billable {
			t.Fatalf("non-event family %d is billable", i)
		}
	}
	enforcement, _ := driver.Normalize(records[2])
	if enforcement.Attributes["numbat.enforcement_observed_only"] != true {
		t.Fatal("enforcement trust marker missing")
	}
	unknown, _ := driver.Normalize(records[6])
	if unknown.Attributes["numbat.known_record_type"] != false || unknown.Billable {
		t.Fatal("future record family must remain non-billable")
	}
}

func TestNumbatOfficialActivityRequiresIdentityAndTime(t *testing.T) {
	for _, line := range []string{
		`{"schema_version":"0.2.0","record_type":"event","timestamp":"2026-07-31T00:00:00Z","event_type":"tool.call"}`,
		`{"schema_version":"0.2.0","record_type":"finding","finding_id":"f1"}`,
		`{"schema_version":"0.2.0","record_type":"enforcement","decision_id":"d1"}`,
	} {
		driver, err := NewNumbatSourceDriver(strings.NewReader(line))
		if err != nil {
			t.Fatal(err)
		}
		raw, _, _ := driver.Acquire(context.Background(), "", 1)
		if _, err := driver.Normalize(raw[0]); err == nil {
			t.Fatalf("accepted incomplete record: %s", line)
		}
	}
}

func TestNumbatMappingClosedUnknownAndPairBilling(t *testing.T) {
	input := strings.NewReader(`
{"schema_version":"0.2.0","event_type":"tool.call","event_id":"call-1","observed_at":"2026-07-30T20:00:00Z","call_id":"c1","parameters":{"token":"secret"}}
{"schema_version":"0.2.0","event_type":"tool.result","event_id":"result-1","observed_at":"2026-07-30T20:00:01Z","call_id":"c1","content":"secret"}
{"schema_version":"0.2.0","event_type":"future.kind","event_id":"future-1","observed_at":"2026-07-30T20:00:02Z"}
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
