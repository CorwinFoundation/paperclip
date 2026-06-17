package adapter

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (fn roundTripFunc) Do(req *http.Request) (*http.Response, error) {
	return fn(req)
}

func TestDatadogLogsAPIAdapterProjectsEvents(t *testing.T) {
	sink := NewDatadogLogsAPI(DatadogConfig{
		Site:           "https://api.example.test",
		APIKey:         "dd-api",
		ApplicationKey: "dd-app",
		Query:          "service:agent",
		From:           time.Date(2026, 6, 16, 18, 0, 0, 0, time.UTC),
		To:             time.Date(2026, 6, 16, 19, 0, 0, 0, time.UTC),
		Client: roundTripFunc(func(req *http.Request) (*http.Response, error) {
			if req.Method != http.MethodPost {
				t.Fatalf("method = %s", req.Method)
			}
			if req.URL.Path != "/api/v2/logs/events/search" {
				t.Fatalf("path = %s", req.URL.Path)
			}
			if req.Header.Get("DD-API-KEY") != "dd-api" || req.Header.Get("DD-APPLICATION-KEY") != "dd-app" {
				t.Fatalf("missing datadog auth headers")
			}
			return jsonResponse(`{"data":[{"attributes":{"message":"{\"event_type\":\"tool_call\",\"event_id\":\"evt-dd\",\"logged_at\":\"2026-06-16T18:00:00Z\",\"payload\":{\"tool\":\"pay\"}}"}}]}`), nil
		}),
	})
	records := collectRecords(t, sink)
	if len(records) != 1 || records[0].VendorPayload["event_id"] != "evt-dd" {
		t.Fatalf("unexpected records: %#v", records)
	}
}

func TestSplunkRESTExportAdapterProjectsEvents(t *testing.T) {
	sink := NewSplunkRESTExport(SplunkConfig{
		BaseURL: "https://splunk.example.test",
		Token:   "splunk-token",
		Search:  `search index=agent`,
		Client: roundTripFunc(func(req *http.Request) (*http.Response, error) {
			if req.URL.Query().Get("output_mode") != "json" {
				t.Fatalf("output_mode query missing: %s", req.URL.RawQuery)
			}
			if req.Header.Get("Authorization") != "Splunk splunk-token" {
				t.Fatalf("authorization header mismatch")
			}
			return jsonResponse(`{"result":{"_raw":"{\"event_type\":\"error\",\"event_id\":\"evt-sp\",\"logged_at\":\"2026-06-16T18:00:00Z\",\"payload\":{\"message\":\"x\"}}"}}`), nil
		}),
	})
	records := collectRecords(t, sink)
	if len(records) != 1 || records[0].VendorPayload["event_id"] != "evt-sp" {
		t.Fatalf("unexpected records: %#v", records)
	}
}

func TestCloudWatchFilterLogEventsAdapterProjectsEvents(t *testing.T) {
	sink := NewCloudWatchFilterLogEvents(CloudWatchConfig{
		Endpoint:      "https://logs.us-east-1.amazonaws.com",
		Authorization: "AWS4-HMAC-SHA256 signed",
		LogGroupName:  "/aws/agent",
		FilterPattern: "tool_call",
		Client: roundTripFunc(func(req *http.Request) (*http.Response, error) {
			if req.Header.Get("X-Amz-Target") != "Logs_20140328.FilterLogEvents" {
				t.Fatalf("target header mismatch")
			}
			if req.Header.Get("Authorization") != "AWS4-HMAC-SHA256 signed" {
				t.Fatalf("authorization header mismatch")
			}
			return jsonResponse(`{"events":[{"eventId":"evt-cw","timestamp":1781632800000,"message":"{\"event_type\":\"llm_completion\",\"payload\":{\"model\":\"pinned\"}}"}]}`), nil
		}),
	})
	records := collectRecords(t, sink)
	if len(records) != 1 || records[0].VendorPayload["event_id"] != "evt-cw" {
		t.Fatalf("unexpected records: %#v", records)
	}
	if records[0].VendorPayload["logged_at"] != "2026-06-16T18:00:00Z" {
		t.Fatalf("logged_at fallback mismatch: %#v", records[0].VendorPayload["logged_at"])
	}
}

func collectRecords(t *testing.T, sink LogSinkAdapter) []RawRecord {
	t.Helper()
	stream, err := sink.Stream(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	records := []RawRecord{}
	for record := range stream {
		records = append(records, record)
	}
	return records
}

func jsonResponse(body string) *http.Response {
	return &http.Response{
		StatusCode: http.StatusOK,
		Status:     "200 OK",
		Body:       io.NopCloser(strings.NewReader(body)),
		Header:     http.Header{"Content-Type": []string{"application/json"}},
	}
}
