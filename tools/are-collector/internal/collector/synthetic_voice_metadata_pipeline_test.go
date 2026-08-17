package collector

import (
	"context"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"backbond/are-collector/internal/egress"
	"backbond/are-collector/internal/schemav1"
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

func TestSyntheticVoiceMetadataBatchChainVerifierRejectsMutation(t *testing.T) {
	batchPublicKey, batchKey, _ := ed25519.GenerateKey(strings.NewReader(strings.Repeat("c", 64)))
	events := []schemav1.Event{schemav1.NewEvent("evt-chain", schemav1.EventToolCall, "2026-08-08T12:00:00Z", schemav1.SourceDatadog, map[string]any{"tool": "synthetic_voice_metadata"})}
	first, err := egress.NewSignedChainedBatch("synthetic", "agent-fixture", "batch-1", "batch-key", egress.GenesisBatchHash, time.Unix(0, 0).UTC(), events, batchKey)
	if err != nil {
		t.Fatal(err)
	}
	second, err := egress.NewSignedChainedBatch("synthetic", "agent-fixture", "batch-2", "batch-key", first.BatchHash, time.Unix(1, 0).UTC(), events, batchKey)
	if err != nil {
		t.Fatal(err)
	}
	third, err := egress.NewSignedChainedBatch("synthetic", "agent-fixture", "batch-3", "batch-key", second.BatchHash, time.Unix(2, 0).UTC(), events, batchKey)
	if err != nil {
		t.Fatal(err)
	}
	valid := []egress.ChainedBatch{first, second, third}
	if err := verifySyntheticVoiceBatchChain(valid, batchPublicKey); err != nil {
		t.Fatalf("valid chain rejected: %v", err)
	}
	tampered := append([]egress.ChainedBatch(nil), valid...)
	tampered[1].BatchID = "batch-tampered"
	deleted := []egress.ChainedBatch{first, third}
	reordered := []egress.ChainedBatch{second, first, third}
	fork, err := egress.NewSignedChainedBatch("synthetic", "agent-fixture", "batch-fork", "batch-key", first.BatchHash, time.Unix(3, 0).UTC(), events, batchKey)
	if err != nil {
		t.Fatal(err)
	}
	forked := []egress.ChainedBatch{first, second, fork}
	for name, batches := range map[string][]egress.ChainedBatch{
		"tamper": tampered, "deletion": deleted, "reordering": reordered, "fork": forked,
	} {
		t.Run(name, func(t *testing.T) {
			if err := verifySyntheticVoiceBatchChain(batches, batchPublicKey); err == nil {
				t.Fatalf("accepted %s", name)
			}
		})
	}
}

func verifySyntheticVoiceBatchChain(batches []egress.ChainedBatch, publicKey ed25519.PublicKey) error {
	expectedPrevious := egress.GenesisBatchHash
	for index, batch := range batches {
		if batch.PrevBatchHash != expectedPrevious {
			return fmt.Errorf("batch[%d] predecessor mismatch", index)
		}
		parts := strings.Split(batch.JWT, ".")
		if len(parts) != 3 {
			return fmt.Errorf("batch[%d] malformed JWT", index)
		}
		signature, err := base64.RawURLEncoding.DecodeString(parts[2])
		if err != nil || !ed25519.Verify(publicKey, []byte(parts[0]+"."+parts[1]), signature) {
			return fmt.Errorf("batch[%d] invalid signature", index)
		}
		payload, err := base64.RawURLEncoding.DecodeString(parts[1])
		if err != nil {
			return fmt.Errorf("batch[%d] invalid claims", index)
		}
		var claims map[string]any
		if err := json.Unmarshal(payload, &claims); err != nil {
			return fmt.Errorf("batch[%d] invalid claims: %w", index, err)
		}
		chainBody := map[string]any{
			"customer_id": batch.CustomerID, "agent": batch.AgentID, "schema_version": batch.SchemaVersion,
			"wire_version": batch.WireVersion, "batch_id": batch.BatchID, "batch_sha256": batch.BatchSHA256,
			"prev_batch_hash": batch.PrevBatchHash, "event_count": batch.EventCount, "issued_at": batch.IssuedAt,
		}
		canonical, err := schemav1.CanonicalJSON(chainBody)
		if err != nil {
			return err
		}
		digest := sha256.Sum256(canonical)
		expectedHash := "sha256:" + hex.EncodeToString(digest[:])
		if batch.BatchHash != expectedHash || claims["batch_hash"] != expectedHash ||
			claims["prev_batch_hash"] != batch.PrevBatchHash || claims["batch_id"] != batch.BatchID {
			return fmt.Errorf("batch[%d] envelope/claims/hash mismatch", index)
		}
		expectedPrevious = batch.BatchHash
	}
	return nil
}
