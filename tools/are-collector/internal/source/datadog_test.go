package source

import (
	"context"
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
		return response(200, `{"data":[{"id":"evt-1","type":"log","attributes":{"timestamp":"2026-09-09T00:00:00Z","message":"SECRET","attributes":{"session_id":"s","prompt":"SECRET","event_type":"agent_invocation"}}}],"meta":{"page":{"after":"next"}}}`, r), nil
	})})
	if err != nil {
		t.Fatal(err)
	}
	rows, next, err := d.Acquire(context.Background(), "", 10)
	if err != nil || next != "next" || len(rows) != 1 {
		t.Fatalf("rows=%v next=%q err=%v", rows, next, err)
	}
	n, err := d.Normalize(rows[0])
	if err != nil {
		t.Fatal(err)
	}
	if n.Attributes["prompt"] != nil || n.Attributes["message"] != nil || n.SessionID != "s" || n.Attributes["datadog.trajectory_coverage"] != false {
		t.Fatalf("normalized=%+v", n)
	}
}
func TestDatadogDriverFailsClosed(t *testing.T) {
	for _, code := range []int{401, 403, 429, 500} {
		d, _ := NewDatadogSourceDriver(DatadogConfig{Site: "https://api.datadoghq.com", APIKey: "a", ApplicationKey: "b", Client: roundTrip(func(r *http.Request) (*http.Response, error) { return response(code, `{}`, r), nil })})
		if _, _, err := d.Acquire(context.Background(), "keep", 1); err == nil {
			t.Fatalf("code %d accepted", code)
		}
	}
	if _, err := NewDatadogSourceDriver(DatadogConfig{Site: "http://api.datadoghq.com", APIKey: "a", ApplicationKey: "b"}); err == nil {
		t.Fatal("http site accepted")
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
