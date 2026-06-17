package egress

import (
	"crypto/ed25519"
	"strings"
	"testing"
	"time"

	"backbond/are-collector/internal/schemav1"
)

func TestSignedBatchUsesSchemaPinnedEventsAndJWTEnvelope(t *testing.T) {
	_, privateKey, err := ed25519.GenerateKey(strings.NewReader(strings.Repeat("a", 64)))
	if err != nil {
		t.Fatal(err)
	}
	event := schemav1.NewEvent(
		"evt-1",
		schemav1.EventToolCall,
		"2026-06-16T18:00:00Z",
		schemav1.SourceDatadog,
		map[string]any{"tool": "payments"},
	)
	batch, err := NewSignedBatch(
		"customer-1",
		"agent-1",
		"batch-1",
		time.Date(2026, 6, 16, 18, 1, 0, 0, time.UTC),
		[]schemav1.Event{event},
		privateKey,
	)
	if err != nil {
		t.Fatal(err)
	}
	if batch.SchemaVersion != schemav1.Version {
		t.Fatalf("schema version mismatch: %s", batch.SchemaVersion)
	}
	if batch.EventCount != 1 {
		t.Fatalf("event count mismatch: %d", batch.EventCount)
	}
	if !strings.HasPrefix(batch.BatchSHA256, "sha256:") {
		t.Fatalf("batch sha missing prefix: %s", batch.BatchSHA256)
	}
	if strings.Count(batch.JWT, ".") != 2 {
		t.Fatalf("jwt is not compact serialization: %s", batch.JWT)
	}
}
