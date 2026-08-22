package main

import (
	"bytes"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"

	"backbond/are-collector/internal/egress"
)

func testPrivateKeyB64(t *testing.T) string {
	t.Helper()
	_, privateKey, err := ed25519.GenerateKey(strings.NewReader(strings.Repeat("a", 64)))
	if err != nil {
		t.Fatal(err)
	}
	return base64.StdEncoding.EncodeToString(privateKey)
}

// Negative control: make Append never match a prior row (`for false && scanner.Scan()`).
// Observed: "replay duplicated output" at main_test.go:46, the replay re-emitting a full batch.
func TestCLINumbatReconcilesAndReplayIsIdempotent(t *testing.T) {
	receiptPath := t.TempDir() + "/receipts.ndjson"
	input := `{"schema_version":"0.2.0","record_type":"event","event_id":"e1","timestamp":"2026-07-31T00:00:00Z","event_type":"tool.call","content":"private prompt"}`
	args := []string{"-mode", "fixture-smoke", "-source", "numbat", "-customer-id", "tenant-1", "-agent-id", "agent-1", "-deployment-id", "deploy-1", "-batch-id", "batch-1", "-key-id", "key-1", "-ed25519-private-key-b64", testPrivateKeyB64(t), "-reconciliation-receipt", receiptPath, "-numbat-commit", "83f8c74f", "-numbat-schema", "0.2.0"}
	for runNumber := 0; runNumber < 2; runNumber++ {
		var out bytes.Buffer
		if err := run(args, strings.NewReader(input), &out); err != nil {
			t.Fatal(err)
		}
		if strings.Contains(out.String(), "private prompt") {
			t.Fatalf("raw content leaked: %s", out.String())
		}
		var result map[string]any
		if err := json.Unmarshal(out.Bytes(), &result); err != nil {
			t.Fatal(err)
		}
		receipt := result["reconciliation_receipt"].(map[string]any)
		if receipt["source_count"] != receipt["emitted_count"] {
			t.Fatalf("unreconciled receipt: %#v", receipt)
		}
		if runNumber == 1 && (result["batch"] != nil || receipt["replay_outcome"] != "idempotent_replay") {
			t.Fatalf("replay duplicated output: %#v", result)
		}
	}
	raw, err := os.ReadFile(receiptPath)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Count(strings.TrimSpace(string(raw)), "\n") != 0 {
		t.Fatalf("duplicate receipt lines: %s", raw)
	}
}

// Negative control: bind signed.BatchSHA256 instead of signed.BatchHash on the v2
// branch of main.go. Observed: "receipt binds sha256:726b4e78..., want v2 chain
// identity sha256:efac71e5..." at main_test.go:93.
func TestCLINumbatWireV2BindsChainIdentityAndKeepsDistinctBatches(t *testing.T) {
	receiptPath := t.TempDir() + "/receipts.ndjson"
	input := `{"schema_version":"0.2.0","record_type":"event","event_id":"e1","timestamp":"2026-07-31T00:00:00Z","event_type":"tool.call","content":"private prompt"}`
	numbatArgs := func(batchID, prevBatchHash string) []string {
		return []string{"-mode", "fixture-smoke", "-source", "numbat", "-customer-id", "tenant-1", "-agent-id", "agent-1", "-deployment-id", "deploy-1", "-batch-id", batchID, "-key-id", "key-1", "-ed25519-private-key-b64", testPrivateKeyB64(t), "-reconciliation-receipt", receiptPath, "-numbat-commit", "83f8c74f", "-numbat-schema", "0.2.0", "-wire-version", egress.WireVersionV2, "-prev-batch-hash", prevBatchHash}
	}
	drive := func(args []string) map[string]any {
		t.Helper()
		var out bytes.Buffer
		if err := run(args, strings.NewReader(input), &out); err != nil {
			t.Fatal(err)
		}
		if strings.Contains(out.String(), "private prompt") {
			t.Fatalf("raw content leaked: %s", out.String())
		}
		var result map[string]any
		if err := json.Unmarshal(out.Bytes(), &result); err != nil {
			t.Fatal(err)
		}
		return result
	}
	receiptLines := func() int {
		t.Helper()
		raw, err := os.ReadFile(receiptPath)
		if err != nil {
			t.Fatal(err)
		}
		return len(strings.Split(strings.TrimSpace(string(raw)), "\n"))
	}

	first := drive(numbatArgs("batch-1", egress.GenesisBatchHash))
	batch := first["batch"].(map[string]any)
	receipt := first["reconciliation_receipt"].(map[string]any)
	firstHash := batch["batch_hash"].(string)
	if got := receipt["ordered_batch_hashes"].([]any)[0].(string); got != firstHash {
		t.Fatalf("receipt binds %q, want v2 chain identity %q (batch_sha256 is %q)", got, firstHash, batch["batch_sha256"])
	}
	if receipt["wire_version"] != egress.WireVersionV2 {
		t.Fatalf("receipt wire_version = %v, want %q", receipt["wire_version"], egress.WireVersionV2)
	}
	if receipt["replay_outcome"] != "accepted" {
		t.Fatalf("first run not accepted: %#v", receipt)
	}

	// A distinct batch that chains onto the first must be accepted, not deduped:
	// its events are identical, so only the chain identity distinguishes it.
	second := drive(numbatArgs("batch-2", firstHash))
	secondReceipt := second["reconciliation_receipt"].(map[string]any)
	if second["batch"] == nil || secondReceipt["replay_outcome"] != "accepted" {
		t.Fatalf("distinct v2 batch was deduped away: %#v", second)
	}
	secondHash := second["batch"].(map[string]any)["batch_hash"].(string)
	if secondHash == firstHash {
		t.Fatalf("chained batches share identity %q", firstHash)
	}
	if got := secondReceipt["ordered_batch_hashes"].([]any)[0].(string); got != secondHash {
		t.Fatalf("receipt binds %q, want %q", got, secondHash)
	}
	if lines := receiptLines(); lines != 2 {
		t.Fatalf("receipt has %d lines, want 2", lines)
	}

	// Replaying the first batch verbatim is still idempotent.
	replay := drive(numbatArgs("batch-1", egress.GenesisBatchHash))
	if replay["batch"] != nil || replay["reconciliation_receipt"].(map[string]any)["replay_outcome"] != "idempotent_replay" {
		t.Fatalf("v2 replay was not idempotent: %#v", replay)
	}
	if lines := receiptLines(); lines != 2 {
		t.Fatalf("replay appended a row: receipt has %d lines, want 2", lines)
	}
}

// The v2 batch identity commits to issued_at, so a replay that lands in a later
// wall-clock second emits a different batch_hash. Replay protection must survive
// that; a test whose runs all finish inside one second cannot prove it does.
// Negative control: key reconciliation.Append on ordered_batch_hashes[0] instead of
// the idempotency key. Observed: the post-bind-egress-wire-v2 subtest fails, because
// batch_hash commits to issued_at and moves between the two runs. The v1 subtest still
// passes under that control, which is why this test runs as subtests over both wires.
func TestCLINumbatReplayIsIdempotentAcrossASecondBoundary(t *testing.T) {
	for _, wire := range []string{"post-bind-egress-wire-v1", egress.WireVersionV2} {
		t.Run(wire, func(t *testing.T) {
			receiptPath := t.TempDir() + "/receipts.ndjson"
			input := `{"schema_version":"0.2.0","record_type":"event","event_id":"e1","timestamp":"2026-07-31T00:00:00Z","event_type":"tool.call","content":"private prompt"}`
			args := []string{"-mode", "fixture-smoke", "-source", "numbat", "-customer-id", "tenant-1", "-agent-id", "agent-1", "-deployment-id", "deploy-1", "-batch-id", "batch-1", "-key-id", "key-1", "-ed25519-private-key-b64", testPrivateKeyB64(t), "-reconciliation-receipt", receiptPath, "-numbat-commit", "83f8c74f", "-numbat-schema", "0.2.0", "-wire-version", wire, "-prev-batch-hash", egress.GenesisBatchHash}
			drive := func() map[string]any {
				t.Helper()
				var out bytes.Buffer
				if err := run(args, strings.NewReader(input), &out); err != nil {
					t.Fatal(err)
				}
				var result map[string]any
				if err := json.Unmarshal(out.Bytes(), &result); err != nil {
					t.Fatal(err)
				}
				return result
			}
			first := drive()
			firstReceipt := first["reconciliation_receipt"].(map[string]any)
			time.Sleep(1100 * time.Millisecond)
			replay := drive()
			replayReceipt := replay["reconciliation_receipt"].(map[string]any)
			if replay["batch"] != nil {
				t.Fatalf("replay across a second boundary re-emitted the batch: %v", replay["batch"].(map[string]any)["batch_hash"])
			}
			if replayReceipt["replay_outcome"] != "idempotent_replay" {
				t.Fatalf("replay_outcome = %v, want idempotent_replay", replayReceipt["replay_outcome"])
			}
			if replayReceipt["idempotency_key"] != firstReceipt["idempotency_key"] {
				t.Fatalf("idempotency key moved between replays: %v then %v", firstReceipt["idempotency_key"], replayReceipt["idempotency_key"])
			}
			raw, err := os.ReadFile(receiptPath)
			if err != nil {
				t.Fatal(err)
			}
			if lines := len(strings.Split(strings.TrimSpace(string(raw)), "\n")); lines != 1 {
				t.Fatalf("replay appended a row: receipt has %d lines, want 1", lines)
			}
		})
	}
}

// Two batches over the same events but under different batch ids are distinct on
// either wire. On v1 the emitted batch_sha256 is identical for both, so keying
// dedupe on it alone drops the second one.
// Negative control: key reconciliation.Append on ordered_batch_hashes[0] instead of
// the idempotency key. Observed: the post-bind-egress-wire-v1 subtest fails, because
// on v1 that value is the event digest alone and both batch ids share it.
func TestCLINumbatDistinctBatchIDsAreNotDeduped(t *testing.T) {
	for _, wire := range []string{"post-bind-egress-wire-v1", egress.WireVersionV2} {
		t.Run(wire, func(t *testing.T) {
			receiptPath := t.TempDir() + "/receipts.ndjson"
			input := `{"schema_version":"0.2.0","record_type":"event","event_id":"e1","timestamp":"2026-07-31T00:00:00Z","event_type":"tool.call","content":"private prompt"}`
			drive := func(batchID string) map[string]any {
				t.Helper()
				var out bytes.Buffer
				args := []string{"-mode", "fixture-smoke", "-source", "numbat", "-customer-id", "tenant-1", "-agent-id", "agent-1", "-deployment-id", "deploy-1", "-batch-id", batchID, "-key-id", "key-1", "-ed25519-private-key-b64", testPrivateKeyB64(t), "-reconciliation-receipt", receiptPath, "-numbat-commit", "83f8c74f", "-numbat-schema", "0.2.0", "-wire-version", wire, "-prev-batch-hash", egress.GenesisBatchHash}
				if err := run(args, strings.NewReader(input), &out); err != nil {
					t.Fatal(err)
				}
				var result map[string]any
				if err := json.Unmarshal(out.Bytes(), &result); err != nil {
					t.Fatal(err)
				}
				return result
			}
			first := drive("batch-1")
			second := drive("batch-2")
			if second["batch"] == nil || second["reconciliation_receipt"].(map[string]any)["replay_outcome"] != "accepted" {
				t.Fatalf("a distinct batch id was deduped away: %#v", second["reconciliation_receipt"])
			}
			if first["batch"].(map[string]any)["batch_sha256"] != second["batch"].(map[string]any)["batch_sha256"] {
				t.Fatal("fixture no longer exercises the collision: event digests differ")
			}
			raw, err := os.ReadFile(receiptPath)
			if err != nil {
				t.Fatal(err)
			}
			if lines := len(strings.Split(strings.TrimSpace(string(raw)), "\n")); lines != 2 {
				t.Fatalf("receipt has %d lines, want 2", lines)
			}
		})
	}
}

// A monitor-only collection exercises no rubric point, so the receipt must say
// exactly that. "monitor-only" is not a rubric id — rubric ids are shaped P1.1 or
// PILLAR.FAMILY.NAME — and the N=5 release gate rejects it, so a receipt naming it
// asserts a rubric point that does not exist on an artifact a customer reconciles
// against (BEAAA-24615, ruling BEAAA-24451).
//
// Negative control: restore RubricPointID: "monitor-only" in main.go. Observed at
// main_test.go:250: "rubric_point_id = monitor-only, want none-exercised".
func TestCLINumbatReceiptClaimsNoRubricPoint(t *testing.T) {
	receiptPath := t.TempDir() + "/receipts.ndjson"
	input := `{"schema_version":"0.2.0","record_type":"event","event_id":"e1","timestamp":"2026-07-31T00:00:00Z","event_type":"tool.call","content":"private prompt"}`
	args := []string{"-mode", "fixture-smoke", "-source", "numbat", "-customer-id", "tenant-1", "-agent-id", "agent-1", "-deployment-id", "deploy-1", "-batch-id", "batch-1", "-key-id", "key-1", "-ed25519-private-key-b64", testPrivateKeyB64(t), "-reconciliation-receipt", receiptPath, "-numbat-commit", "83f8c74f", "-numbat-schema", "0.2.0"}
	var out bytes.Buffer
	if err := run(args, strings.NewReader(input), &out); err != nil {
		t.Fatal(err)
	}
	var result map[string]any
	if err := json.Unmarshal(out.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	receipt := result["reconciliation_receipt"].(map[string]any)
	if receipt["rubric_point_id"] != "none-exercised" {
		t.Fatalf("rubric_point_id = %v, want none-exercised", receipt["rubric_point_id"])
	}
	// The key and the schema version are deliberately untouched by BEAAA-24615.
	if receipt["receipt_version"] != "numbat-reconciliation-receipt-v3" {
		t.Fatalf("receipt_version moved: %v", receipt["receipt_version"])
	}
}

// Negative control: make the commit pin check vacuous (compare the flag to itself).
// Observed at main_test.go:266: "pin mismatch read source: err=numbat record requires
// record_type or legacy event_type and observed_at reads=2" — the spool was read before
// the mismatch was caught.
func TestCLINumbatPinMismatchFailsBeforeRead(t *testing.T) {
	reader := &countingReader{Reader: strings.NewReader(`{"schema_version":"0.2.0"}`)}
	err := run([]string{"-mode", "fixture-smoke", "-source", "numbat", "-customer-id", "t", "-agent-id", "a", "-deployment-id", "d", "-batch-id", "b", "-key-id", "k", "-ed25519-private-key-b64", testPrivateKeyB64(t), "-reconciliation-receipt", t.TempDir() + "/r", "-numbat-commit", "wrong", "-numbat-schema", "0.2.0"}, reader, &bytes.Buffer{})
	if err == nil || reader.reads != 0 {
		t.Fatalf("pin mismatch read source: err=%v reads=%d", err, reader.reads)
	}
}

type countingReader struct {
	*strings.Reader
	reads int
}

func (r *countingReader) Read(p []byte) (int, error) { r.reads++; return r.Reader.Read(p) }

func TestCLIEmitsSignedBatch(t *testing.T) {
	input := strings.NewReader(`{"event_type":"tool_call","event_id":"evt-1","logged_at":"2026-06-16T18:00:00Z","payload":{"args":{"email":"jane@example.com"}}}`)
	out := bytes.Buffer{}
	err := run(
		[]string{
			"-mode", "fixture-smoke",
			"-source", "datadog",
			"-customer-id", "customer-1",
			"-agent-id", "agent-1",
			"-batch-id", "batch-1",
			"-key-id", "bind-key-1",
			"-ed25519-private-key-b64", testPrivateKeyB64(t),
			"-policy-json", `{"args.email":"redact-on-receipt"}`,
		},
		input,
		&out,
	)
	if err != nil {
		t.Fatal(err)
	}
	var batch map[string]any
	if err := json.Unmarshal(out.Bytes(), &batch); err != nil {
		t.Fatal(err)
	}
	if batch["schema_version"] != "log-event-schema-v1.0" {
		t.Fatalf("schema_version mismatch: %#v", batch["schema_version"])
	}
	if batch["jwt"] == "" {
		t.Fatal("missing jwt")
	}
}

func TestCLIRejectsUnwiredRealMode(t *testing.T) {
	err := run(
		[]string{
			"-mode", "datadog",
			"-source", "datadog",
			"-customer-id", "customer-1",
			"-agent-id", "agent-1",
			"-batch-id", "batch-1",
			"-key-id", "bind-key-1",
			"-ed25519-private-key-b64", testPrivateKeyB64(t),
		},
		strings.NewReader(`{}`),
		&bytes.Buffer{},
	)
	if err == nil || !strings.Contains(err.Error(), "only fixture-smoke is enabled") {
		t.Fatalf("expected unwired mode error, got %v", err)
	}
}

func TestCLIRejectsMissingPrivateKey(t *testing.T) {
	err := run(
		[]string{
			"-mode", "fixture-smoke",
			"-source", "datadog",
			"-customer-id", "customer-1",
			"-agent-id", "agent-1",
			"-batch-id", "batch-1",
			"-key-id", "bind-key-1",
		},
		strings.NewReader(`{}`),
		&bytes.Buffer{},
	)
	if err == nil || !strings.Contains(err.Error(), "ed25519-private-key-b64 is required") {
		t.Fatalf("expected private key error, got %v", err)
	}
}
