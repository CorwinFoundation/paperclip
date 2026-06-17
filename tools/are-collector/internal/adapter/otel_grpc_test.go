package adapter

import (
	"context"
	"testing"

	collectorlogspb "go.opentelemetry.io/proto/otlp/collector/logs/v1"
	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	logspb "go.opentelemetry.io/proto/otlp/logs/v1"
)

func TestOTELGRPCReceiverExportsLogRecords(t *testing.T) {
	receiver := NewOTELGRPCReceiver("127.0.0.1:0")
	_, err := receiver.Export(context.Background(), &collectorlogspb.ExportLogsServiceRequest{
		ResourceLogs: []*logspb.ResourceLogs{
			{
				ScopeLogs: []*logspb.ScopeLogs{
					{
						LogRecords: []*logspb.LogRecord{
							{
								TimeUnixNano: 1781632800000000000,
								Body:         stringValue(`{"event_type":"tool_call","event_id":"evt-otel","logged_at":"2026-06-16T18:00:00Z","payload":{"tool":"search"}}`),
								Attributes: []*commonpb.KeyValue{
									{Key: "ignored_after_json_body", Value: stringValue("x")},
								},
							},
						},
					},
				},
			},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	record := <-receiver.records
	if record.SourceKind != "otel" {
		t.Fatalf("source kind = %s", record.SourceKind)
	}
	if record.VendorPayload["event_id"] != "evt-otel" {
		t.Fatalf("payload = %#v", record.VendorPayload)
	}
}

func stringValue(value string) *commonpb.AnyValue {
	return &commonpb.AnyValue{
		Value: &commonpb.AnyValue_StringValue{StringValue: value},
	}
}
