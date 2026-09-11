package source

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"
)

type roundTrip func(*http.Request) (*http.Response, error)

func (f roundTrip) Do(r *http.Request) (*http.Response, error) { return f(r) }
func response(code int, body string, req *http.Request) *http.Response {
	return &http.Response{StatusCode: code, Body: io.NopCloser(strings.NewReader(body)), Request: req}
}

func TestDatadogDriverPaginationAndFieldMinimization(t *testing.T) {
	d, err := NewDatadogSourceDriver(DatadogConfig{Site: "https://api.datadoghq.com", APIKey: "api", ApplicationKey: "app", Client: roundTrip(func(r *http.Request) (*http.Response, error) {
		if r.URL.Host != "api.datadoghq.com" || r.URL.Path != "/api/v2/logs/events/search" {
			t.Fatalf("url=%s", r.URL)
		}
		return response(200, `{"data":[{"id":"evt-1","type":"log","attributes":{"timestamp":"2026-09-09T00:00:00Z","message":"SECRET","attributes":{"session_id":"s","prompt":"SECRET","event_type":"agent_invocation"}}}],"meta":{"page":{"after":"next-page-token"}}}`, r), nil
	})})
	if err != nil {
		t.Fatal(err)
	}
	rows, nextCursor, err := d.Acquire(context.Background(), "", 10)
	if err != nil || len(rows) != 1 {
		t.Fatalf("rows=%v err=%v", rows, err)
	}
	// Cursor must encode the mid-window page token, not be the raw Datadog cursor string.
	c := decodeCursorOrFail(t, nextCursor)
	if c.PageCursor != "next-page-token" {
		t.Fatalf("expected page cursor=next-page-token, got %q", c.PageCursor)
	}
	if c.WindowTo == "" {
		t.Fatal("WindowTo must be set mid-window")
	}

	n, err := d.Normalize(rows[0])
	if err != nil {
		t.Fatal(err)
	}
	if n.Attributes["prompt"] != nil || n.Attributes["message"] != nil || n.SessionID != "s" || n.Attributes["datadog.trajectory_coverage"] != false {
		t.Fatalf("normalized=%+v", n)
	}
}

func TestDatadogDriverCursorAdvancement(t *testing.T) {
	// When Datadog returns no page.after, the window is complete and the cursor must
	// advance AfterTS to the latest event timestamp seen.
	d, err := NewDatadogSourceDriver(DatadogConfig{Site: "datadoghq.com", APIKey: "api", ApplicationKey: "app", Client: roundTrip(func(r *http.Request) (*http.Response, error) {
		return response(200, `{"data":[
			{"id":"e1","type":"log","attributes":{"timestamp":"2026-09-09T00:00:01Z"}},
			{"id":"e2","type":"log","attributes":{"timestamp":"2026-09-09T00:00:03Z"}},
			{"id":"e3","type":"log","attributes":{"timestamp":"2026-09-09T00:00:03Z"}}
		],"meta":{}}`, r), nil
	})})
	if err != nil {
		t.Fatal(err)
	}
	rows, nextCursor, err := d.Acquire(context.Background(), "", 10)
	if err != nil || len(rows) != 3 {
		t.Fatalf("rows=%d err=%v", len(rows), err)
	}
	c := decodeCursorOrFail(t, nextCursor)
	if c.AfterTS != "2026-09-09T00:00:03Z" {
		t.Fatalf("AfterTS=%q, want 2026-09-09T00:00:03Z", c.AfterTS)
	}
	if len(c.AfterIDs) != 2 || !containsAll(c.AfterIDs, "e2", "e3") {
		t.Fatalf("AfterIDs=%v, want [e2 e3]", c.AfterIDs)
	}
	if c.PageCursor != "" {
		t.Fatalf("PageCursor must be empty after window completion, got %q", c.PageCursor)
	}
}

func TestDatadogDriverBoundaryDedup(t *testing.T) {
	// Events at exactly AfterTS with IDs in AfterIDs must be filtered out.
	// Events at AfterTS with new IDs must be delivered.
	prior := datadogCursor{
		AfterTS:  "2026-09-09T00:00:03Z",
		AfterIDs: []string{"e2", "e3"},
	}
	priorEncoded := encodeDatadogCursor(prior)

	d, err := NewDatadogSourceDriver(DatadogConfig{Site: "datadoghq.com", APIKey: "api", ApplicationKey: "app", Client: roundTrip(func(r *http.Request) (*http.Response, error) {
		// New window returns: e2 (boundary dup), e3 (boundary dup), e4 (new at boundary ts), e5 (new, later)
		return response(200, `{"data":[
			{"id":"e2","type":"log","attributes":{"timestamp":"2026-09-09T00:00:03Z"}},
			{"id":"e3","type":"log","attributes":{"timestamp":"2026-09-09T00:00:03Z"}},
			{"id":"e4","type":"log","attributes":{"timestamp":"2026-09-09T00:00:03Z"}},
			{"id":"e5","type":"log","attributes":{"timestamp":"2026-09-09T00:00:05Z"}}
		],"meta":{}}`, r), nil
	})})
	if err != nil {
		t.Fatal(err)
	}
	rows, nextCursor, err := d.Acquire(context.Background(), priorEncoded, 10)
	if err != nil {
		t.Fatal(err)
	}
	// e2 and e3 are boundary dups, should be filtered. e4 and e5 are new.
	if len(rows) != 2 {
		t.Fatalf("expected 2 rows (e4, e5), got %d: %v", len(rows), offsets(rows))
	}
	ids := offsets(rows)
	if !containsAll(ids, "e4", "e5") {
		t.Fatalf("unexpected rows: %v", ids)
	}
	c := decodeCursorOrFail(t, nextCursor)
	if c.AfterTS != "2026-09-09T00:00:05Z" {
		t.Fatalf("AfterTS=%q, want 2026-09-09T00:00:05Z", c.AfterTS)
	}
}

func TestDatadogDriverWindowUsesCheckpoint(t *testing.T) {
	// When a prior cursor has AfterTS, Acquire must use it as "from", not "now-15m".
	prior := datadogCursor{AfterTS: "2026-09-09T00:00:00Z", AfterIDs: []string{"e0"}}
	priorEncoded := encodeDatadogCursor(prior)

	var capturedFrom string
	d, err := NewDatadogSourceDriver(DatadogConfig{Site: "datadoghq.com", APIKey: "api", ApplicationKey: "app", Client: roundTrip(func(r *http.Request) (*http.Response, error) {
		var body map[string]any
		json.NewDecoder(r.Body).Decode(&body)
		filter, _ := body["filter"].(map[string]any)
		capturedFrom, _ = filter["from"].(string)
		return response(200, `{"data":[],"meta":{}}`, r), nil
	})})
	if err != nil {
		t.Fatal(err)
	}
	d.Acquire(context.Background(), priorEncoded, 10)
	if capturedFrom != "2026-09-09T00:00:00Z" {
		t.Fatalf("expected from=%q (checkpoint), got %q", "2026-09-09T00:00:00Z", capturedFrom)
	}
}

func TestDatadogDriverBareSite(t *testing.T) {
	// Bare DATADOG_SITE convention: "datadoghq.com" → endpoint at api.datadoghq.com
	d, err := NewDatadogSourceDriver(DatadogConfig{Site: "datadoghq.com", APIKey: "a", ApplicationKey: "b", Client: roundTrip(func(r *http.Request) (*http.Response, error) {
		if r.URL.Host != "api.datadoghq.com" {
			t.Fatalf("expected host api.datadoghq.com, got %q", r.URL.Host)
		}
		return response(200, `{"data":[],"meta":{}}`, r), nil
	})})
	if err != nil {
		t.Fatalf("bare site rejected: %v", err)
	}
	if _, _, err := d.Acquire(context.Background(), "", 1); err != nil {
		t.Fatal(err)
	}
}

func TestDatadogDriverFailsClosed(t *testing.T) {
	for _, code := range []int{401, 403, 429, 500} {
		d, _ := NewDatadogSourceDriver(DatadogConfig{Site: "https://api.datadoghq.com", APIKey: "a", ApplicationKey: "b", Client: roundTrip(func(r *http.Request) (*http.Response, error) { return response(code, `{}`, r), nil })})
		if _, _, err := d.Acquire(context.Background(), "keep", 1); err == nil {
			t.Fatalf("code %d accepted", code)
		}
	}
	// http:// must be rejected
	if _, err := NewDatadogSourceDriver(DatadogConfig{Site: "http://api.datadoghq.com", APIKey: "a", ApplicationKey: "b"}); err == nil {
		t.Fatal("http site accepted")
	}
	// corrupt checkpoint must fail closed
	d2, _ := NewDatadogSourceDriver(DatadogConfig{Site: "https://api.datadoghq.com", APIKey: "a", ApplicationKey: "b", Client: roundTrip(func(r *http.Request) (*http.Response, error) { return response(200, `{}`, r), nil })})
	if _, _, err := d2.Acquire(context.Background(), "!!!notbase64!!!", 1); err == nil {
		t.Fatal("corrupt checkpoint accepted")
	}
}

func TestConfiguredRegistryResolvesDatadogFromEnvironment(t *testing.T) {
	t.Setenv("DATADOG_SITE", "https://api.datadoghq.com")
	t.Setenv("DD_API_KEY", "api")
	t.Setenv("DD_APP_KEY", "app")
	t.Setenv("DATADOG_QUERY", "service:controlled-agent")

	driver, err := ConfiguredRegistry(RegistryConfig{}).Resolve("datadog", strings.NewReader("fixture input must not be consumed"))
	if err != nil {
		t.Fatal(err)
	}
	if got := driver.Descriptor().DriverID; got != "datadog" {
		t.Fatalf("driver id=%q", got)
	}
}

func TestConfiguredRegistryResolvesDatadogBareSite(t *testing.T) {
	t.Setenv("DATADOG_SITE", "datadoghq.com")
	t.Setenv("DD_API_KEY", "api")
	t.Setenv("DD_APP_KEY", "app")

	driver, err := ConfiguredRegistry(RegistryConfig{}).Resolve("datadog", strings.NewReader(""))
	if err != nil {
		t.Fatal(err)
	}
	if got := driver.Descriptor().DriverID; got != "datadog" {
		t.Fatalf("driver id=%q", got)
	}
}

// helpers

func decodeCursorOrFail(t *testing.T, s string) datadogCursor {
	t.Helper()
	b, err := base64.RawURLEncoding.DecodeString(s)
	if err != nil {
		t.Fatalf("cursor not base64: %v (cursor=%q)", err, s)
	}
	var c datadogCursor
	if err := json.Unmarshal(b, &c); err != nil {
		t.Fatalf("cursor not JSON: %v", err)
	}
	return c
}

func offsets(rows []RawEnvelope) []string {
	out := make([]string, len(rows))
	for i, r := range rows {
		out[i] = r.Offset
	}
	return out
}

func containsAll(ss []string, vals ...string) bool {
	m := make(map[string]bool, len(ss))
	for _, s := range ss {
		m[s] = true
	}
	for _, v := range vals {
		if !m[v] {
			return false
		}
	}
	return true
}
