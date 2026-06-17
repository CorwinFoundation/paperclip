package adapter

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"

	"backbond/are-collector/internal/schemav1"
)

type RawRecord struct {
	SourceKind    schemav1.SourceKind
	VendorPayload map[string]any
	VendorMeta    map[string]string
}

type LogSinkAdapter interface {
	SourceKind() schemav1.SourceKind
	Stream(ctx context.Context) (<-chan RawRecord, error)
	Healthcheck(ctx context.Context) error
}

type HTTPDoer interface {
	Do(req *http.Request) (*http.Response, error)
}

type JSONLinesAdapter struct {
	source schemav1.SourceKind
	reader io.Reader
}

func NewJSONLinesAdapter(source schemav1.SourceKind, reader io.Reader) *JSONLinesAdapter {
	return &JSONLinesAdapter{source: source, reader: reader}
}

func (adapter *JSONLinesAdapter) SourceKind() schemav1.SourceKind {
	return adapter.source
}

func (adapter *JSONLinesAdapter) Healthcheck(ctx context.Context) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	default:
	}
	if adapter.reader == nil {
		return errors.New("adapter reader is required")
	}
	return nil
}

func (adapter *JSONLinesAdapter) Stream(ctx context.Context) (<-chan RawRecord, error) {
	if err := adapter.Healthcheck(ctx); err != nil {
		return nil, err
	}
	decoder := json.NewDecoder(adapter.reader)
	records := make(chan RawRecord)
	go func() {
		defer close(records)
		for {
			select {
			case <-ctx.Done():
				return
			default:
			}
			var payload map[string]any
			if err := decoder.Decode(&payload); err != nil {
				if errors.Is(err, io.EOF) {
					return
				}
				return
			}
			records <- RawRecord{
				SourceKind:    adapter.source,
				VendorPayload: payload,
				VendorMeta: map[string]string{
					"adapter": string(adapter.source),
				},
			}
		}
	}()
	return records, nil
}

func NewDatadogJSONLines(reader io.Reader) LogSinkAdapter {
	return NewJSONLinesAdapter(schemav1.SourceDatadog, reader)
}

func NewSplunkJSONLines(reader io.Reader) LogSinkAdapter {
	return NewJSONLinesAdapter(schemav1.SourceSplunk, reader)
}

func NewCloudWatchJSONLines(reader io.Reader) LogSinkAdapter {
	return NewJSONLinesAdapter(schemav1.SourceCloudWatch, reader)
}

func NewOTELJSONLines(reader io.Reader) LogSinkAdapter {
	return NewJSONLinesAdapter(schemav1.SourceOTEL, reader)
}

func AdapterFromFixture(source string, reader io.Reader) (LogSinkAdapter, error) {
	switch strings.ToLower(strings.TrimSpace(source)) {
	case "datadog":
		return NewDatadogJSONLines(reader), nil
	case "splunk":
		return NewSplunkJSONLines(reader), nil
	case "cloudwatch":
		return NewCloudWatchJSONLines(reader), nil
	case "otel":
		return NewOTELJSONLines(reader), nil
	default:
		return nil, fmt.Errorf("unsupported source kind: %q", source)
	}
}
