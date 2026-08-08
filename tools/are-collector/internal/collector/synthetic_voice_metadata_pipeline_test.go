package collector

import (
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"backbond/are-collector/internal/egress"
	"backbond/are-collector/internal/source"
)

func TestSyntheticVoiceMetadataUsesCanonicalPipelineAndHashChain(t *testing.T) {
	publicKey, eventKey, _ := ed25519.GenerateKey(strings.NewReader(strings.Repeat("s", 64)))
	event := map[string]any{
		"source_event_id": "voice-evt-pipeline", "observed_at": "2026-08-08T12:00:00Z",
		"agent_id": "agent-fixture", "deployment_id": "deploy-fixture", "count": 1,
		"action_class": "message", "outcome_status": "completed",
		"determinations": []any{map[string]any{"class": "availability", "result": "pass"}},
		"commitments":    map[string]any{"evidence_sha256": "sha256:" + strings.Repeat("a", 64)},
		"continuity":     map[string]any{"sequence": 1, "previous_event_sha256": egress.GenesisBatchHash},
		"producer":       map[string]any{"collector_version": "0.5.0", "driver_version": source.SyntheticVoiceMetadataMappingVersion, "scanner_version": "0.5.0", "corpus_version": "0.1.0", "seed": 0},
	}
	canonical, _ := json.Marshal(event)
	envelope, _ := json.Marshal(map[string]any{"key_id": "fixture", "event": event, "signature": base64.RawURLEncoding.EncodeToString(ed25519.Sign(eventKey, canonical))})
	driver, err := source.NewSyntheticVoiceMetadataSourceDriver(strings.NewReader(string(envelope)+"\n"), map[string]ed25519.PublicKey{"fixture": publicKey})
	if err != nil {
		t.Fatal(err)
	}
	events, err := (Pipeline{}).CollectDriver(context.Background(), driver, 10)
	if err != nil {
		t.Fatal(err)
	}
	_, batchKey, _ := ed25519.GenerateKey(strings.NewReader(strings.Repeat("b", 64)))
	first, err := egress.NewSignedChainedBatch("synthetic", "agent-fixture", "batch-1", "batch-key", egress.GenesisBatchHash, time.Unix(0, 0).UTC(), events, batchKey)
	if err != nil {
		t.Fatal(err)
	}
	second, err := egress.NewSignedChainedBatch("synthetic", "agent-fixture", "batch-2", "batch-key", first.BatchHash, time.Unix(1, 0).UTC(), events, batchKey)
	if err != nil {
		t.Fatal(err)
	}
	if second.PrevBatchHash != first.BatchHash {
		t.Fatalf("chain link mismatch: %s != %s", second.PrevBatchHash, first.BatchHash)
	}
	firstAgain, err := egress.NewSignedChainedBatch("synthetic", "agent-fixture", "batch-1", "batch-key", egress.GenesisBatchHash, time.Unix(0, 0).UTC(), events, batchKey)
	if err != nil {
		t.Fatal(err)
	}
	a, _ := json.Marshal(first)
	b, _ := json.Marshal(firstAgain)
	if string(a) != string(b) {
		t.Fatal("canonical signed batch was not deterministic")
	}
}
