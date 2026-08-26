package egress

import (
	"bytes"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"backbond/are-collector/internal/schemav1"
)

func TestCanonicalJSONAdversarialFixturePreservesReceiverBytes(t *testing.T) {
	raw, err := os.ReadFile("../../testdata/canonical_json_adversarial_events.json")
	if err != nil {
		t.Fatal(err)
	}
	var events []schemav1.Event
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if err := decoder.Decode(&events); err != nil {
		t.Fatal(err)
	}
	canonical, err := schemav1.CanonicalJSON(events)
	if err != nil {
		t.Fatal(err)
	}
	text := string(canonical)
	if strings.Contains(text, `\u003c`) || strings.Contains(text, `\u003e`) || strings.Contains(text, `\u0026`) {
		t.Fatalf("canonical JSON HTML-escaped receiver-significant bytes: %s", text)
	}
	if !strings.Contains(text, "9007199254740993") {
		t.Fatalf("canonical JSON lost the exact integer: %s", text)
	}
	if got, err := ComputeBatchSHA256(events); err != nil || got == "" {
		t.Fatalf("ComputeBatchSHA256() = %q, %v", got, err)
	}
}
