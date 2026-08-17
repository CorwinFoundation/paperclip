package collector

// QA BEAAA-20074 R4 — finding F1 probe. NOT part of any candidate; do not merge.
// Copy to tools/are-collector/internal/collector/ in a throwaway worktree at
// 89932217ebfadd2d0b6dbe4c6f233a09a61500b4 and run:
//
//   go test ./internal/collector/ -run TestQAProbeProducerVersionStringsCarryContentIntoSignedBatch -v
//
// Question: can prohibited voice content ride the three unconstrained producer.*
// version strings of the BEAAA-20704 metadata driver into a signed, emitted batch?
// Answer at 89932217: yes, on all three fields.

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

func qaProbeEvent(producer map[string]any) map[string]any {
	return map[string]any{
		"source_event_id": "44444444-4444-4444-8444-444444444444", "observed_at": "2026-08-08T12:00:00Z",
		"agent_id": "55555555-5555-4555-8555-555555555555", "deployment_id": "66666666-6666-4666-8666-666666666666", "count": 1,
		"action_class": "message_send", "outcome_status": "completed",
		"determinations": []any{map[string]any{"class": "availability", "result": "pass"}},
		"commitments":    map[string]any{"evidence_sha256": "sha256:" + strings.Repeat("a", 64)},
		"continuity":     map[string]any{"sequence": 1, "previous_event_sha256": egress.GenesisBatchHash},
		"producer":       producer,
	}
}

func TestQAProbeProducerVersionStringsCarryContentIntoSignedBatch(t *testing.T) {
	const transcript = "TRANSCRIPT: caller Jane Doe said my SSN is 123-45-6789 and my card is 4111 1111 1111 1111, recording at https://cdn.example.com/calls/rec-77.mp3, phone +1-415-555-0132"

	cases := map[string]map[string]any{
		"scanner_version": {
			"collector_version": "0.5.0", "driver_version": source.SyntheticVoiceMetadataMappingVersion,
			"scanner_version": transcript, "corpus_version": "0.1.0", "seed": 0,
		},
		"collector_version": {
			"collector_version": transcript, "driver_version": source.SyntheticVoiceMetadataMappingVersion,
			"scanner_version": "0.5.0", "corpus_version": "0.1.0", "seed": 0,
		},
		"corpus_version": {
			"collector_version": "0.5.0", "driver_version": source.SyntheticVoiceMetadataMappingVersion,
			"scanner_version": "0.5.0", "corpus_version": transcript, "seed": 0,
		},
	}

	for field, producer := range cases {
		t.Run(field, func(t *testing.T) {
			event := qaProbeEvent(producer)
			publicKey, eventKey, _ := ed25519.GenerateKey(strings.NewReader(strings.Repeat("s", 64)))
			canonical, _ := json.Marshal(event)
			envelope, _ := json.Marshal(map[string]any{
				"key_id": "fixture", "event": event,
				"signature": base64.RawURLEncoding.EncodeToString(ed25519.Sign(eventKey, canonical)),
			})

			// Layer 1: the driver's own validation.
			driver, err := source.NewSyntheticVoiceMetadataSourceDriver(
				strings.NewReader(string(envelope)+"\n"),
				map[string]ed25519.PublicKey{"fixture": publicKey})
			if err != nil {
				t.Logf("REJECTED at driver construction: %v", err)
				return
			}
			t.Logf("ACCEPTED at driver construction (no rejection)")

			// Layer 2: survival through Normalize into the normalized event.
			events, err := (Pipeline{}).CollectDriver(context.Background(), driver, 10)
			if err != nil {
				t.Logf("REJECTED in pipeline: %v", err)
				return
			}
			normJSON, _ := json.Marshal(events)
			if !strings.Contains(string(normJSON), "123-45-6789") {
				t.Logf("content did NOT survive into normalized events")
				return
			}
			t.Logf("LEAK: content present in normalized events (%d bytes)", len(normJSON))

			// Layer 3: the signed, emitted batch.
			_, batchKey, _ := ed25519.GenerateKey(strings.NewReader(strings.Repeat("b", 64)))
			batch, err := egress.NewSignedChainedBatch("synthetic", "agent-fixture", "batch-1", "batch-key",
				egress.GenesisBatchHash, time.Unix(0, 0).UTC(), events, batchKey)
			if err != nil {
				t.Logf("REJECTED at egress: %v", err)
				return
			}
			batchJSON, _ := json.Marshal(batch)
			for _, marker := range []string{"123-45-6789", "4111 1111 1111 1111", "cdn.example.com", "+1-415-555-0132", "TRANSCRIPT"} {
				if strings.Contains(string(batchJSON), marker) {
					t.Errorf("LEAK IN SIGNED BATCH via producer.%s: marker %q present", field, marker)
				}
			}
			t.Logf("signed batch bytes: %d", len(batchJSON))
		})
	}
}
