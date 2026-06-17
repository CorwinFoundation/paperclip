package adapter

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net"
	"time"

	collectorlogspb "go.opentelemetry.io/proto/otlp/collector/logs/v1"
	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	logspb "go.opentelemetry.io/proto/otlp/logs/v1"
	"google.golang.org/grpc"

	"backbond/are-collector/internal/schemav1"
)

type OTELGRPCReceiver struct {
	collectorlogspb.UnimplementedLogsServiceServer

	addr    string
	server  *grpc.Server
	records chan RawRecord
}

func NewOTELGRPCReceiver(addr string) *OTELGRPCReceiver {
	return &OTELGRPCReceiver{
		addr:    addr,
		records: make(chan RawRecord, 1024),
	}
}

func (receiver *OTELGRPCReceiver) SourceKind() schemav1.SourceKind {
	return schemav1.SourceOTEL
}

func (receiver *OTELGRPCReceiver) Healthcheck(ctx context.Context) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	default:
	}
	if receiver.addr == "" {
		return fmt.Errorf("otel grpc receiver addr is required")
	}
	return nil
}

func (receiver *OTELGRPCReceiver) Stream(ctx context.Context) (<-chan RawRecord, error) {
	if err := receiver.Healthcheck(ctx); err != nil {
		return nil, err
	}
	if receiver.server == nil {
		listener, err := net.Listen("tcp", receiver.addr)
		if err != nil {
			return nil, err
		}
		receiver.server = grpc.NewServer()
		collectorlogspb.RegisterLogsServiceServer(receiver.server, receiver)
		go func() {
			<-ctx.Done()
			receiver.server.GracefulStop()
			close(receiver.records)
		}()
		go func() {
			if err := receiver.server.Serve(listener); err != nil {
				close(receiver.records)
			}
		}()
	}
	return receiver.records, nil
}

func (receiver *OTELGRPCReceiver) Export(ctx context.Context, req *collectorlogspb.ExportLogsServiceRequest) (*collectorlogspb.ExportLogsServiceResponse, error) {
	for _, resourceLogs := range req.GetResourceLogs() {
		for _, scopeLogs := range resourceLogs.GetScopeLogs() {
			for _, record := range scopeLogs.GetLogRecords() {
				select {
				case <-ctx.Done():
					return nil, ctx.Err()
				case receiver.records <- RawRecord{
					SourceKind:    schemav1.SourceOTEL,
					VendorPayload: otelLogRecordPayload(record),
					VendorMeta:    map[string]string{"adapter": string(schemav1.SourceOTEL)},
				}:
				}
			}
		}
	}
	return &collectorlogspb.ExportLogsServiceResponse{}, nil
}

func otelLogRecordPayload(record *logspb.LogRecord) map[string]any {
	payload := map[string]any{}
	if record.TimeUnixNano != 0 {
		payload["logged_at"] = time.Unix(0, int64(record.TimeUnixNano)).UTC().Format(time.RFC3339)
	} else if record.ObservedTimeUnixNano != 0 {
		payload["logged_at"] = time.Unix(0, int64(record.ObservedTimeUnixNano)).UTC().Format(time.RFC3339)
	}
	body := anyValue(record.Body)
	if raw, ok := body.(string); ok {
		var nested map[string]any
		if err := json.Unmarshal([]byte(raw), &nested); err == nil {
			return nested
		}
		payload["message"] = raw
	} else if body != nil {
		payload["body"] = body
	}
	for _, attr := range record.Attributes {
		payload[attr.Key] = anyValue(attr.Value)
	}
	if len(record.TraceId) != 0 {
		payload["trace_id"] = hex.EncodeToString(record.TraceId)
	}
	if len(record.SpanId) != 0 {
		payload["span_id"] = hex.EncodeToString(record.SpanId)
	}
	return payload
}

func anyValue(value *commonpb.AnyValue) any {
	if value == nil {
		return nil
	}
	switch typed := value.GetValue().(type) {
	case *commonpb.AnyValue_StringValue:
		return typed.StringValue
	case *commonpb.AnyValue_BoolValue:
		return typed.BoolValue
	case *commonpb.AnyValue_IntValue:
		return typed.IntValue
	case *commonpb.AnyValue_DoubleValue:
		return typed.DoubleValue
	case *commonpb.AnyValue_BytesValue:
		return hex.EncodeToString(typed.BytesValue)
	case *commonpb.AnyValue_ArrayValue:
		out := make([]any, 0, len(typed.ArrayValue.GetValues()))
		for _, item := range typed.ArrayValue.GetValues() {
			out = append(out, anyValue(item))
		}
		return out
	case *commonpb.AnyValue_KvlistValue:
		out := map[string]any{}
		for _, item := range typed.KvlistValue.GetValues() {
			out[item.Key] = anyValue(item.Value)
		}
		return out
	default:
		return nil
	}
}
