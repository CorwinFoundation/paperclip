package collector

import (
	"context"
	"errors"
	"fmt"
	"testing"
	"time"

	"backbond/are-collector/internal/source"
)

type memoryStore struct {
	value string
	saves []string
}

func (s *memoryStore) Load(context.Context, string) (string, error) { return s.value, nil }
func (s *memoryStore) Save(_ context.Context, _ string, cursor string) error {
	s.value = cursor
	s.saves = append(s.saves, cursor)
	return nil
}

type acquireResult struct {
	records []source.RawEnvelope
	next    string
	err     error
}

type fakeDriver struct {
	results       []acquireResult
	checkpoint    string
	checkpointErr error
	acquireCalls  []string
	commits       []string
	normalizeErr  error
}

func (d *fakeDriver) Descriptor() source.Descriptor {
	return source.Descriptor{DriverID: "fake", SourcePin: "fake@1"}
}
func (d *fakeDriver) Checkpoint(context.Context) (string, error) {
	return d.checkpoint, d.checkpointErr
}
func (d *fakeDriver) Acquire(_ context.Context, cursor string, _ int) ([]source.RawEnvelope, string, error) {
	d.acquireCalls = append(d.acquireCalls, cursor)
	if len(d.results) == 0 {
		return nil, cursor, nil
	}
	r := d.results[0]
	d.results = d.results[1:]
	return r.records, r.next, r.err
}
func (d *fakeDriver) Normalize(raw source.RawEnvelope) (source.NormalizedEvent, error) {
	if d.normalizeErr != nil {
		return source.NormalizedEvent{}, d.normalizeErr
	}
	return source.NormalizedEvent{
		SourceEventID: raw.Offset, ObservedAt: "2026-09-09T00:00:00Z", SessionID: "s",
		AgentID: "a", DeploymentID: "d", OperationName: "op", ActionClass: "read",
		OutcomeStatus: "ok", MappingVersion: "fake-v1",
		Attributes: map[string]any{"schema_event_type": "tool_call"},
	}, nil
}
func (d *fakeDriver) Commit(_ context.Context, cursor string) error {
	d.commits = append(d.commits, cursor)
	return nil
}

func record(id string) source.RawEnvelope {
	return source.RawEnvelope{Offset: id, Payload: map[string]any{}}
}
func runtimeFor(store CheckpointStore) Runtime {
	return Runtime{Store: store, PageSize: 2, MaxPages: 10, MaxRetries: 2,
		Backoff: func(int) time.Duration { return 0 }, Sleep: func(context.Context, time.Duration) error { return nil }}
}

func TestRuntimePaginationDedupAndRestartRecovery(t *testing.T) {
	store := &memoryStore{value: "saved"}
	driver := &fakeDriver{checkpoint: "driver", results: []acquireResult{
		{records: []source.RawEnvelope{record("a"), record("b")}, next: "page-2"},
		{records: []source.RawEnvelope{record("b"), record("c")}, next: "done"},
		{records: nil, next: "done"},
	}}
	got, err := runtimeFor(store).Collect(context.Background(), driver)
	if err != nil {
		t.Fatal(err)
	}
	if fmt.Sprint(driver.acquireCalls) != "[saved page-2 done]" {
		t.Fatalf("cursors = %v", driver.acquireCalls)
	}
	if len(got.Events) != 3 || got.DroppedEvents != 1 || got.Cursor != "done" || got.Health != Connected {
		t.Fatalf("result = %+v", got)
	}
	if fmt.Sprint(store.saves) != "[page-2 done]" {
		t.Fatalf("saved = %v", store.saves)
	}
}

func TestRuntimeEmptyWindow(t *testing.T) {
	store := &memoryStore{}
	driver := &fakeDriver{checkpoint: "start", results: []acquireResult{{next: "start"}}}
	got, err := runtimeFor(store).Collect(context.Background(), driver)
	if err != nil || got.Health != Empty || len(store.saves) != 0 {
		t.Fatalf("result=%+v err=%v", got, err)
	}
}

func TestRuntimeFailureStatesPreserveCheckpoint(t *testing.T) {
	for _, tc := range []struct {
		name   string
		kind   FailureKind
		health HealthState
	}{
		{"401_403", FailureAuth, AuthDenied}, {"malformed_payload", FailureSchema, SchemaIncompatible},
	} {
		t.Run(tc.name, func(t *testing.T) {
			store := &memoryStore{value: "keep"}
			driver := &fakeDriver{results: []acquireResult{{err: &SourceError{Kind: tc.kind, Err: errors.New(tc.name)}}}}
			got, err := runtimeFor(store).Collect(context.Background(), driver)
			if err == nil || got.Health != tc.health || store.value != "keep" || len(store.saves) != 0 {
				t.Fatalf("result=%+v err=%v store=%+v", got, err, store)
			}
		})
	}
}

func TestRuntimeNormalizationFailurePreservesCheckpoint(t *testing.T) {
	store := &memoryStore{value: "keep"}
	driver := &fakeDriver{normalizeErr: errors.New("bad schema"), results: []acquireResult{{records: []source.RawEnvelope{record("bad")}, next: "lost"}}}
	got, err := runtimeFor(store).Collect(context.Background(), driver)
	if err == nil || got.Health != SchemaIncompatible || store.value != "keep" || len(driver.commits) != 0 {
		t.Fatalf("result=%+v err=%v", got, err)
	}
}

func TestRuntimeRetriesRateLimitAndServerErrors(t *testing.T) {
	for _, tc := range []struct {
		name string
		kind FailureKind
	}{
		{"429", FailureRateLimit}, {"5xx", FailureTransient},
	} {
		t.Run(tc.name, func(t *testing.T) {
			store := &memoryStore{value: "start"}
			driver := &fakeDriver{results: []acquireResult{
				{err: &SourceError{Kind: tc.kind, Err: errors.New(tc.name)}},
				{records: []source.RawEnvelope{record("a")}, next: "done"},
			}}
			got, err := runtimeFor(store).Collect(context.Background(), driver)
			if err != nil || got.Health != Connected || len(driver.acquireCalls) != 2 || store.value != "done" {
				t.Fatalf("result=%+v err=%v", got, err)
			}
		})
	}
}

func TestRuntimeExhaustedFailuresAreObservable(t *testing.T) {
	for _, tc := range []struct {
		name   string
		kind   FailureKind
		health HealthState
	}{
		{"429", FailureRateLimit, RateLimited}, {"5xx", FailureTransient, Stalled},
	} {
		t.Run(tc.name, func(t *testing.T) {
			store := &memoryStore{value: "keep"}
			driver := &fakeDriver{results: []acquireResult{
				{err: &SourceError{Kind: tc.kind, Err: errors.New(tc.name)}},
				{err: &SourceError{Kind: tc.kind, Err: errors.New(tc.name)}},
				{err: &SourceError{Kind: tc.kind, Err: errors.New(tc.name)}},
			}}
			got, err := runtimeFor(store).Collect(context.Background(), driver)
			if err == nil || got.Health != tc.health || store.value != "keep" {
				t.Fatalf("result=%+v err=%v", got, err)
			}
		})
	}
}
