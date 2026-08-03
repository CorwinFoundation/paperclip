package egress

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"backbond/are-collector/internal/schemav1"
)

func validEvent() schemav1.Event {
	return schemav1.NewEvent("evt-chain", schemav1.EventToolCall, "2026-08-03T20:00:00Z", schemav1.SourceDatadog, map[string]any{"tool": "lookup"})
}

func TestChainedBatchBindsClosedPredecessorIntoSignature(t *testing.T) {
	publicKey, privateKey, _ := ed25519.GenerateKey(nil)
	events := []schemav1.Event{validEvent()}
	batch, err := NewSignedChainedBatch("customer", "agent", "batch-2", "key-1", GenesisBatchHash, time.Now().UTC(), events, privateKey)
	if err != nil {
		t.Fatal(err)
	}
	if batch.WireVersion != WireVersionV2 || batch.PrevBatchHash != GenesisBatchHash {
		t.Fatalf("unexpected v2 envelope: %+v", batch)
	}
	parts := strings.Split(batch.JWT, ".")
	payload, _ := base64.RawURLEncoding.DecodeString(parts[1])
	var claims map[string]any
	if err := json.Unmarshal(payload, &claims); err != nil {
		t.Fatal(err)
	}
	if claims["prev_batch_hash"] != GenesisBatchHash {
		t.Fatalf("predecessor missing from claims: %+v", claims)
	}
	sig, _ := base64.RawURLEncoding.DecodeString(parts[2])
	if !ed25519.Verify(publicKey, []byte(parts[0]+"."+parts[1]), sig) {
		t.Fatal("signature did not verify")
	}
	claims["prev_batch_hash"] = "sha256:" + strings.Repeat("1", 64)
	tampered, _ := json.Marshal(claims)
	if ed25519.Verify(publicKey, []byte(parts[0]+"."+base64.RawURLEncoding.EncodeToString(tampered)), sig) {
		t.Fatal("tampered predecessor verified")
	}
}

func TestChainedBatchRejectsOpenPredecessorForms(t *testing.T) {
	_, privateKey, _ := ed25519.GenerateKey(nil)
	for _, value := range []string{"", "genesis", "sha256:ABC", "sha256:" + strings.Repeat("A", 64)} {
		if _, err := NewSignedChainedBatch("customer", "agent", "batch", "key", value, time.Now(), []schemav1.Event{validEvent()}, privateKey); err == nil {
			t.Fatalf("accepted %q", value)
		}
	}
}

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
		"bind-key-1",
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
