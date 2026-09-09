package collector

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"backbond/are-collector/internal/schemav1"
	"backbond/are-collector/internal/source"
)

type HealthState string

const (
	Connected          HealthState = "connected"
	Empty              HealthState = "empty"
	Stalled            HealthState = "stalled"
	AuthDenied         HealthState = "auth_denied"
	RateLimited        HealthState = "rate_limited"
	SchemaIncompatible HealthState = "schema_incompatible"
)

type FailureKind string

const (
	FailureAuth      FailureKind = "auth"
	FailureRateLimit FailureKind = "rate_limit"
	FailureSchema    FailureKind = "schema"
	FailureTransient FailureKind = "transient"
)

type SourceError struct {
	Kind FailureKind
	Err  error
}

func (e *SourceError) Error() string { return e.Err.Error() }
func (e *SourceError) Unwrap() error { return e.Err }

type CheckpointStore interface {
	Load(context.Context, string) (string, error)
	Save(context.Context, string, string) error
}
type FileCheckpointStore struct{ Path string }

func (s FileCheckpointStore) Load(_ context.Context, id string) (string, error) {
	b, err := os.ReadFile(s.Path)
	if errors.Is(err, os.ErrNotExist) {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	var values map[string]string
	if err := json.Unmarshal(b, &values); err != nil {
		return "", fmt.Errorf("checkpoint decode: %w", err)
	}
	return values[id], nil
}
func (s FileCheckpointStore) Save(_ context.Context, id, cursor string) error {
	values := map[string]string{}
	if b, err := os.ReadFile(s.Path); err == nil {
		if err := json.Unmarshal(b, &values); err != nil {
			return err
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	values[id] = cursor
	b, _ := json.Marshal(values)
	if err := os.MkdirAll(filepath.Dir(s.Path), 0700); err != nil {
		return err
	}
	tmp := s.Path + ".tmp"
	if err := os.WriteFile(tmp, b, 0600); err != nil {
		return err
	}
	return os.Rename(tmp, s.Path)
}

type WindowResult struct {
	Events                       []schemav1.Event
	Health                       HealthState
	SchemaVersion, SourceVersion string
	DroppedEvents                int
	Cursor                       string
}
type Runtime struct {
	Pipeline                       Pipeline
	Store                          CheckpointStore
	PageSize, MaxPages, MaxRetries int
	Backoff                        func(int) time.Duration
	Sleep                          func(context.Context, time.Duration) error
}

func (r Runtime) Collect(ctx context.Context, driver source.SourceDriver) (WindowResult, error) {
	if driver == nil || r.Store == nil {
		return WindowResult{}, fmt.Errorf("source driver and checkpoint store are required")
	}
	desc := driver.Descriptor()
	cursor, err := r.Store.Load(ctx, desc.DriverID)
	if err != nil {
		return WindowResult{}, err
	}
	if cursor == "" {
		cursor, err = driver.Checkpoint(ctx)
		if err != nil {
			return WindowResult{}, err
		}
	}
	pageSize, maxPages := r.PageSize, r.MaxPages
	if pageSize <= 0 {
		pageSize = 100
	}
	if maxPages <= 0 {
		maxPages = 100
	}
	result := WindowResult{Health: Empty, SourceVersion: desc.SourcePin, SchemaVersion: source.InterfaceVersion, Cursor: cursor}
	seen := map[string]struct{}{}
	for page := 0; page < maxPages; page++ {
		var records []source.RawEnvelope
		var next string
		for attempt := 0; ; attempt++ {
			records, next, err = driver.Acquire(ctx, cursor, pageSize)
			if err == nil {
				break
			}
			var se *SourceError
			if errors.As(err, &se) {
				switch se.Kind {
				case FailureAuth:
					result.Health = AuthDenied
					return result, err
				case FailureSchema:
					result.Health = SchemaIncompatible
					return result, err
				case FailureRateLimit:
					result.Health = RateLimited
				}
				if se.Kind != FailureRateLimit && se.Kind != FailureTransient {
					return result, err
				}
			}
			if attempt >= r.MaxRetries {
				if result.Health != RateLimited {
					result.Health = Stalled
				}
				return result, err
			}
			d := time.Duration(1<<attempt) * 100 * time.Millisecond
			if r.Backoff != nil {
				d = r.Backoff(attempt)
			}
			if r.Sleep != nil {
				if err := r.Sleep(ctx, d); err != nil {
					return result, err
				}
			} else {
				select {
				case <-ctx.Done():
					return result, ctx.Err()
				case <-time.After(d):
				}
			}
		}
		if len(records) == 0 {
			break
		}
		events, err := r.Pipeline.normalizeRecords(records, driver)
		if err != nil {
			result.Health = SchemaIncompatible
			return result, &SourceError{Kind: FailureSchema, Err: err}
		}
		for _, event := range events {
			if _, ok := seen[event.EventID]; ok {
				result.DroppedEvents++
				continue
			}
			seen[event.EventID] = struct{}{}
			result.Events = append(result.Events, event)
		}
		if err := driver.Commit(ctx, next); err != nil {
			return result, err
		}
		if err := r.Store.Save(ctx, desc.DriverID, next); err != nil {
			return result, err
		}
		result.Cursor, result.Health = next, Connected
		if next == cursor {
			result.Health = Stalled
			break
		}
		cursor = next
		if len(records) < pageSize {
			break
		}
	}
	return result, nil
}
