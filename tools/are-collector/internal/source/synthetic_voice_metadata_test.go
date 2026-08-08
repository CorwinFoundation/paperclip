package source

import (
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"strings"
	"testing"
)

func signedSyntheticVoiceLine(t *testing.T, event map[string]any, extra map[string]any) (string, ed25519.PublicKey) {
	t.Helper()
	pub, priv, err := ed25519.GenerateKey(strings.NewReader(strings.Repeat("v", 64)))
	if err != nil {
		t.Fatal(err)
	}
	canonical, err := json.Marshal(event)
	if err != nil {
		t.Fatal(err)
	}
	envelope := map[string]any{"key_id": "fixture-key", "event": event, "signature": base64.RawURLEncoding.EncodeToString(ed25519.Sign(priv, canonical))}
	for key, value := range extra {
		envelope[key] = value
	}
	raw, err := json.Marshal(envelope)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw) + "\n", pub
}

func validSyntheticVoiceEvent() map[string]any {
	return map[string]any{
		"source_event_id": "voice-evt-1", "observed_at": "2026-08-08T12:00:00Z",
		"agent_id": "agent-fixture", "deployment_id": "deploy-fixture", "count": 1,
		"action_class": "message", "outcome_status": "completed",
		"determinations": []any{map[string]any{"class": "availability", "result": "pass"}},
		"commitments":    map[string]any{"evidence_sha256": "sha256:" + strings.Repeat("a", 64)},
		"continuity":     map[string]any{"sequence": 1, "previous_event_sha256": "sha256:" + strings.Repeat("0", 64)},
		"producer":       map[string]any{"collector_version": "0.5.0", "driver_version": SyntheticVoiceMetadataMappingVersion, "scanner_version": "0.5.0", "corpus_version": "0.1.0", "seed": 0},
	}
}

func TestSyntheticVoiceMetadataDeterministicClosedAndContentFree(t *testing.T) {
	event := validSyntheticVoiceEvent()
	line, pub := signedSyntheticVoiceLine(t, event, nil)
	newDriver := func(input string) *SyntheticVoiceMetadataSourceDriver {
		driver, err := NewSyntheticVoiceMetadataSourceDriver(strings.NewReader(input), map[string]ed25519.PublicKey{"fixture-key": pub})
		if err != nil {
			t.Fatal(err)
		}
		return driver
	}
	first := newDriver(line)
	second := newDriver(line)
	a, _, err := first.Acquire(context.Background(), "", 10)
	if err != nil {
		t.Fatal(err)
	}
	b, _, err := second.Acquire(context.Background(), "", 10)
	if err != nil {
		t.Fatal(err)
	}
	na, err := first.Normalize(a[0])
	if err != nil {
		t.Fatal(err)
	}
	nb, err := second.Normalize(b[0])
	if err != nil {
		t.Fatal(err)
	}
	ja, _ := json.Marshal(na)
	jb, _ := json.Marshal(nb)
	if string(ja) != string(jb) {
		t.Fatalf("non-deterministic output:\n%s\n%s", ja, jb)
	}
	for _, forbidden := range []string{"audio", "recording", "transcript", "text", "content", "prompt", "response", "phone", "tool_argument", "raw_payload", "platform_kind"} {
		if strings.Contains(strings.ToLower(string(ja)), forbidden) {
			t.Fatalf("forbidden token %q in %s", forbidden, ja)
		}
	}
}

func TestSyntheticVoiceMetadataRejectsUnknownAndForbiddenBeforeAcquire(t *testing.T) {
	for _, mutate := range []func(map[string]any){
		func(e map[string]any) { e["transcript"] = "forbidden" },
		func(e map[string]any) { e["unknown"] = true },
		func(e map[string]any) { e["commitments"].(map[string]any)["raw_payload"] = "forbidden" },
		func(e map[string]any) { e["producer"].(map[string]any)["future"] = "forbidden" },
	} {
		event := validSyntheticVoiceEvent()
		mutate(event)
		line, pub := signedSyntheticVoiceLine(t, event, nil)
		if _, err := NewSyntheticVoiceMetadataSourceDriver(strings.NewReader(line), map[string]ed25519.PublicKey{"fixture-key": pub}); err == nil {
			t.Fatal("accepted open or forbidden field")
		}
	}
	event := validSyntheticVoiceEvent()
	line, pub := signedSyntheticVoiceLine(t, event, map[string]any{"unknown": true})
	if _, err := NewSyntheticVoiceMetadataSourceDriver(strings.NewReader(line), map[string]ed25519.PublicKey{"fixture-key": pub}); err == nil {
		t.Fatal("accepted open envelope")
	}
}

func TestSyntheticVoiceMetadataRejectsSignatureReplayAndCheckpointFork(t *testing.T) {
	event := validSyntheticVoiceEvent()
	line, pub := signedSyntheticVoiceLine(t, event, nil)
	tampered := strings.Replace(line, "completed", "failed", 1)
	if _, err := NewSyntheticVoiceMetadataSourceDriver(strings.NewReader(tampered), map[string]ed25519.PublicKey{"fixture-key": pub}); err == nil {
		t.Fatal("accepted tampered signature")
	}
	driver, err := NewSyntheticVoiceMetadataSourceDriver(strings.NewReader(line+line), map[string]ed25519.PublicKey{"fixture-key": pub})
	if err != nil {
		t.Fatal(err)
	}
	records, next, err := driver.Acquire(context.Background(), "", 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 1 {
		t.Fatalf("replay was not idempotent: %d", len(records))
	}
	if err := driver.Commit(context.Background(), next); err != nil {
		t.Fatal(err)
	}
	if _, _, err := driver.Acquire(context.Background(), "2", 10); err == nil {
		t.Fatal("accepted checkpoint fork")
	}
}
