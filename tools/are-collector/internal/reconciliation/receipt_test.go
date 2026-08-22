package reconciliation

import (
	"bytes"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"strings"
	"testing"
	"time"

	"backbond/are-collector/internal/egress"
	"backbond/are-collector/internal/schemav1"
)

// Negative control: remove the source == emitted + quarantine check from Append.
// Observed: "accepted unreconciled counts" at receipt_test.go:14.
func TestAppendRejectsSilentDrop(t *testing.T) {
	_, _, err := Append(t.TempDir()+"/r.ndjson", Receipt{SourceCount: 2, EmittedCount: 1, OrderedBatchHashes: []string{"sha256:x"}, WireVersion: "post-bind-egress-wire-v1", IdempotencyKey: "sha256:k"})
	if err == nil {
		t.Fatal("accepted unreconciled counts")
	}
}

// Negative control: remove the two required-field guards from Append.
// Observed: "accepted a receipt with no wire_version" at receipt_test.go:22.
func TestAppendRequiresWireVersionAndIdempotencyKey(t *testing.T) {
	path := t.TempDir() + "/r.ndjson"
	_, _, err := Append(path, Receipt{SourceCount: 1, EmittedCount: 1, OrderedBatchHashes: []string{"sha256:x"}, IdempotencyKey: "sha256:k"})
	if err == nil {
		t.Fatal("accepted a receipt with no wire_version")
	}
	if _, _, err := Append(path, Receipt{SourceCount: 1, EmittedCount: 1, OrderedBatchHashes: []string{"sha256:x"}, WireVersion: "post-bind-egress-wire-v1"}); err == nil {
		t.Fatal("accepted a receipt with no idempotency_key")
	}
	if _, statErr := os.Stat(path); statErr == nil {
		t.Fatal("rejected receipt still created the file")
	}
}

// Negative control: stop stamping receipt.ReceiptVersion in Append.
// Observed: "receipt did not record its version pins" at receipt_test.go:39.
func TestAppendRecordsWireVersionAndDistinguishesWireIdentities(t *testing.T) {
	path := t.TempDir() + "/r.ndjson"
	v1, replayed, err := Append(path, Receipt{SourceCount: 1, EmittedCount: 1, OrderedBatchHashes: []string{"sha256:aa"}, WireVersion: "post-bind-egress-wire-v1", IdempotencyKey: "sha256:k1"})
	if err != nil || replayed {
		t.Fatalf("first append: err=%v replayed=%v", err, replayed)
	}
	if v1.ReceiptVersion != ReceiptVersion || v1.WireVersion != "post-bind-egress-wire-v1" {
		t.Fatalf("receipt did not record its version pins: %#v", v1)
	}
	v2, replayed, err := Append(path, Receipt{SourceCount: 1, EmittedCount: 1, OrderedBatchHashes: []string{"sha256:bb"}, WireVersion: "post-bind-egress-wire-v2", IdempotencyKey: "sha256:k2"})
	if err != nil || replayed || v2.ReplayOutcome != "accepted" {
		t.Fatalf("distinct chain identity was not accepted: err=%v replayed=%v receipt=%#v", err, replayed, v2)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if lines := len(strings.Split(strings.TrimSpace(string(raw)), "\n")); lines != 2 {
		t.Fatalf("receipt has %d lines, want 2", lines)
	}
}

// The emitted batch identity moves between replays on the v2 wire, because it
// commits to issued_at. Dedupe must follow the idempotency key, not that hash.
// Negative control: key Append on ordered_batch_hashes[0] instead of the idempotency
// key. Observed: the replay carrying a moved batch identity is accepted as a new row.
func TestAppendDedupesOnIdempotencyKeyNotBatchIdentity(t *testing.T) {
	path := t.TempDir() + "/r.ndjson"
	base := Receipt{SourceCount: 1, EmittedCount: 1, WireVersion: "post-bind-egress-wire-v2", IdempotencyKey: IdempotencyKey("t", "a", "d", "post-bind-egress-wire-v2", "b1", "sha256:prev", "sha256:events")}
	first := base
	first.OrderedBatchHashes = []string{"sha256:identity-at-t0"}
	if _, replayed, err := Append(path, first); err != nil || replayed {
		t.Fatalf("first append: err=%v replayed=%v", err, replayed)
	}
	second := base
	second.OrderedBatchHashes = []string{"sha256:identity-at-t1"}
	got, replayed, err := Append(path, second)
	if err != nil || !replayed || got.ReplayOutcome != "idempotent_replay" {
		t.Fatalf("replay with a moved batch identity was not deduped: err=%v replayed=%v receipt=%#v", err, replayed, got)
	}
	distinct := base
	distinct.IdempotencyKey = IdempotencyKey("t", "a", "d", "post-bind-egress-wire-v2", "b2", "sha256:prev", "sha256:events")
	distinct.OrderedBatchHashes = []string{"sha256:identity-b2"}
	if _, replayed, err := Append(path, distinct); err != nil || replayed {
		t.Fatalf("a distinct batch_id was deduped: err=%v replayed=%v", err, replayed)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if lines := len(strings.Split(strings.TrimSpace(string(raw)), "\n")); lines != 2 {
		t.Fatalf("receipt has %d lines, want 2", lines)
	}
}

// The previous encoding escaped "|" as "||" and joined on "|", and the test that
// was meant to prove injectivity picked tuples that happened not to collide. This
// one enumerates every tuple over an alphabet built from the separator, the
// length-prefix delimiter and digits, which is where an encoding of this shape
// breaks if it is going to.
// Negative control: swap the encoding for escape-and-join (the a5cfc756 form).
// Observed: "distinct tuples share a key: [\"\" \"\" \"|\"] and [\"\" \"|\" \"\"]" at receipt_test.go:99.
func TestIdempotencyKeyIsInjective(t *testing.T) {
	alphabet := []string{"", "|", "||", "|||", ":", "1:", "2:a", "a|", "|a", "0", "12", "a"}
	seen := map[string][]string{}
	for _, tenant := range alphabet {
		for _, agent := range alphabet {
			for _, deployment := range alphabet {
				tuple := []string{tenant, agent, deployment}
				key := IdempotencyKey(tenant, agent, deployment, "post-bind-egress-wire-v2", "b1", "sha256:prev", "sha256:events")
				if prior, ok := seen[key]; ok {
					t.Fatalf("distinct tuples share a key: %q and %q", prior, tuple)
				}
				seen[key] = tuple
			}
		}
	}
	// The remaining fields must separate too.
	tail := [][]string{
		{"post-bind-egress-wire-v1", "b1", "sha256:prev", "sha256:events"},
		{"post-bind-egress-wire-v2", "b1", "sha256:prev", "sha256:events"},
		{"post-bind-egress-wire-v2", "b2", "sha256:prev", "sha256:events"},
		{"post-bind-egress-wire-v2", "b1", "", "sha256:events"},
		{"post-bind-egress-wire-v2", "b1", "sha256:prev", "sha256:other"},
		{"post-bind-egress-wire-v2", "b1|", "sha256:prev", "sha256:events"},
		{"post-bind-egress-wire-v2", "b1", "|sha256:prev", "sha256:events"},
	}
	tailSeen := map[string][]string{}
	for _, row := range tail {
		key := IdempotencyKey("t", "a", "d", row[0], row[1], row[2], row[3])
		if prior, ok := tailSeen[key]; ok {
			t.Fatalf("distinct tuples share a key: %q and %q", prior, row)
		}
		tailSeen[key] = row
	}
}

// The exact pair QA drove through the CLI against the escaped-separator encoding.
// Negative control: swap the encoding for escape-and-join. Observed: "a pipe moved
// across the tenant/agent boundary and kept the key" at receipt_test.go:130.
func TestIdempotencyKeySeparatesTenantFromAgent(t *testing.T) {
	first := IdempotencyKey("tenant|", "agent", "d", "post-bind-egress-wire-v2", "b1", "sha256:prev", "sha256:events")
	second := IdempotencyKey("tenant", "|agent", "d", "post-bind-egress-wire-v2", "b1", "sha256:prev", "sha256:events")
	if first == second {
		t.Fatalf("a pipe moved across the tenant/agent boundary and kept the key: %s", first)
	}
}

// brokenEscapeJoinKey is the encoding this package used at proposal a5cfc756:
// escape "|" as "||", join fields on "|". It is kept here only as a control, so
// the enumeration below is shown to be capable of failing. An injectivity test
// that has never failed against a known-broken encoding is not evidence.
func brokenEscapeJoinKey(fields ...string) string {
	escaped := make([]string, len(fields))
	for i, field := range fields {
		escaped[i] = strings.ReplaceAll(field, "|", "||")
	}
	sum := sha256.Sum256([]byte(strings.Join(escaped, "|")))
	return "sha256:" + hex.EncodeToString(sum[:])
}

func TestIdempotencyKeyEnumerationIsInjective(t *testing.T) {
	// The alphabet is built from everything that can confuse a field encoding:
	// the old separator and a doubled form of it, the length delimiter, a digit
	// that could be read as a length prefix, the empty field, a plain byte, and a
	// multi-byte rune — the case that separates byte length from rune count.
	alphabet := []string{"", "|", "||", ":", "1:", "0", "a", "日"}
	fields := make([]string, 7)
	fields[6] = "sha256:events"

	enumerate := func(key func(...string) string) (combinations, collisions int) {
		seen := make(map[string]string, 1<<18)
		var walk func(depth int)
		walk = func(depth int) {
			if depth == 6 {
				combinations++
				k := key(fields...)
				joined := strings.Join(fields, ",")
				if prior, ok := seen[k]; ok && prior != joined {
					collisions++
					return
				}
				seen[k] = joined
				return
			}
			for _, value := range alphabet {
				fields[depth] = value
				walk(depth + 1)
			}
		}
		walk(0)
		return combinations, collisions
	}

	realKey := func(f ...string) string {
		return IdempotencyKey(f[0], f[1], f[2], f[3], f[4], f[5], f[6])
	}
	combinations, collisions := enumerate(realKey)
	if combinations != 262144 {
		t.Fatalf("enumerated %d tuples, want 262144", combinations)
	}
	if collisions != 0 {
		t.Fatalf("length-prefixed encoding collided on %d of %d tuples", collisions, combinations)
	}

	// The same enumeration against the encoding this one replaced. If this does
	// not collide, the enumeration is not exercising the failure it exists to catch.
	if _, controlCollisions := enumerate(brokenEscapeJoinKey); controlCollisions == 0 {
		t.Fatal("control encoding did not collide; the enumeration cannot fail and proves nothing")
	} else {
		t.Logf("control (escape-and-join) collided on %d of %d tuples", controlCollisions, combinations)
	}
}

// legacyV2Row is a receipt line as the v2 schema actually wrote it: receipt_version
// v2, and no idempotency_key member at all. That absence is the whole problem —
// a v3 Append scanning it finds nothing to match and would re-emit a batch the
// file already records.
const legacyV2Row = `{"receipt_version":"numbat-reconciliation-receipt-v2","tenant_id":"t","agent_id":"a","deployment_id":"d","source_count":1,"emitted_count":1,"quarantine_count":0,"non_billable_count":0,"ordered_batch_hashes":["sha256:legacy-identity"],"wire_version":"post-bind-egress-wire-v2","event_totals":{"tool_call":1},"replay_outcome":"accepted","collector_version":"are-collector@legacy","started_at":"2026-08-19T07:00:00Z","completed_at":"2026-08-19T07:00:01Z"}`

// Guard A, BEAAA-24480 acceptance 1. A pre-upgrade receipt file is refused, not
// silently appended to.
// Negative control: delete the `prior.ReceiptVersion != ReceiptVersion` branch
// from Append's scan loop.
// Observed: "appended against a receipt file holding a v2 row" at
// receipt_test.go:255.
// Negative control for the flag-name assertion: put -receipt-path back in the
// guard's recovery text in receipt.go, replacing -reconciliation-receipt.
// Observed: "recovery text does not name the -reconciliation-receipt flag" at
// receipt_test.go:275.
// Negative control for the stale-flag assertion: append "(formerly
// -receipt-path)" to that same text, leaving the correct flag in place.
// Observed: "recovery text names -receipt-path, a flag the collector does not
// define" at receipt_test.go:278.
func TestAppendFailsClosedOnMixedReceiptVersion(t *testing.T) {
	path := t.TempDir() + "/r.ndjson"
	if err := os.WriteFile(path, []byte(legacyV2Row+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	before, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	_, replayed, err := Append(path, Receipt{
		TenantID: "t", AgentID: "a", DeploymentID: "d",
		SourceCount: 1, EmittedCount: 1,
		OrderedBatchHashes: []string{"sha256:identity"}, WireVersion: "post-bind-egress-wire-v2",
		IdempotencyKey: IdempotencyKey("t", "a", "d", "post-bind-egress-wire-v2", "b1", "sha256:prev", "sha256:events"),
		EventTotals:    map[string]int{"tool_call": 1},
	})
	if err == nil {
		t.Fatal("appended against a receipt file holding a v2 row")
	}
	if replayed {
		t.Fatalf("a mixed-version file reported a replay: %v", err)
	}
	// The message has to be actionable at 3am: both versions, and the operation.
	for _, want := range []string{"rotate", "numbat-reconciliation-receipt-v2", ReceiptVersion} {
		if !strings.Contains(err.Error(), want) {
			t.Fatalf("error does not name %q: %v", want, err)
		}
	}
	// BEAAA-24497. Actionable also means the flag it names is one the collector
	// defines. The message shipped -receipt-path, which is in no flagset, so an
	// operator following it literally hit "flag provided but not defined" on top
	// of the failure the guard had just reported. The flagset side is pinned by
	// the CLI tests, which drive run() with the -reconciliation-receipt literal.
	// Space-delimited on both sides: an unanchored substring check would pass on
	// the version strings alone, since "numbat-reconciliation-receipt-v3"
	// contains "-reconciliation-receipt".
	if !strings.Contains(err.Error(), " -reconciliation-receipt ") {
		t.Fatalf("recovery text does not name the -reconciliation-receipt flag: %v", err)
	}
	if strings.Contains(err.Error(), "-receipt-path") {
		t.Fatalf("recovery text names -receipt-path, a flag the collector does not define: %v", err)
	}
	after, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(before) != string(after) {
		t.Fatalf("a refused append modified the file:\nbefore %s\nafter  %s", before, after)
	}
}

// Guard B, BEAAA-24480 acceptance 2. A matching key carrying different
// reconciliation numbers is an integrity signal, not a replay.
// Negative control: delete the `divergentFields` branch from Append.
// Observed: "accepted a emitted_count divergence under a matching key" at
// receipt_test.go:317, and the same line on the event_totals, source_count and
// non_billable_count subtests.
func TestAppendFailsClosedOnDivergentCountsUnderAMatchingKey(t *testing.T) {
	key := IdempotencyKey("t", "a", "d", "post-bind-egress-wire-v2", "b1", "sha256:prev", "sha256:events")
	base := Receipt{
		TenantID: "t", AgentID: "a", DeploymentID: "d",
		SourceCount: 3, EmittedCount: 3, QuarantineCount: 0, NonBillableCount: 1,
		OrderedBatchHashes: []string{"sha256:identity"}, WireVersion: "post-bind-egress-wire-v2",
		IdempotencyKey: key, EventTotals: map[string]int{"tool_call": 2, "llm_completion": 1},
	}
	for _, testCase := range []struct {
		name     string
		mutate   func(Receipt) Receipt
		reported string
	}{
		{
			name:     "emitted_count",
			mutate:   func(r Receipt) Receipt { r.SourceCount, r.EmittedCount = 4, 4; return r },
			reported: "emitted_count prior=3 candidate=4",
		},
		{
			name: "event_totals",
			mutate: func(r Receipt) Receipt {
				r.EventTotals = map[string]int{"tool_call": 3}
				return r
			},
			reported: "event_totals prior={llm_completion=1,tool_call=2} candidate={tool_call=3}",
		},
		{
			name:     "source_count",
			mutate:   func(r Receipt) Receipt { r.SourceCount, r.QuarantineCount = 4, 1; return r },
			reported: "source_count prior=3 candidate=4",
		},
		{
			name:     "non_billable_count",
			mutate:   func(r Receipt) Receipt { r.NonBillableCount = 2; return r },
			reported: "non_billable_count prior=1 candidate=2",
		},
	} {
		t.Run(testCase.name, func(t *testing.T) {
			path := t.TempDir() + "/r.ndjson"
			if _, replayed, err := Append(path, base); err != nil || replayed {
				t.Fatalf("first append: err=%v replayed=%v", err, replayed)
			}
			_, replayed, err := Append(path, testCase.mutate(base))
			if err == nil {
				t.Fatalf("accepted a %s divergence under a matching key", testCase.name)
			}
			if replayed {
				t.Fatalf("a divergent row was reported as a replay: %v", err)
			}
			if !strings.Contains(err.Error(), testCase.reported) {
				t.Fatalf("error does not report the divergence as %q: %v", testCase.reported, err)
			}
			raw, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			if lines := len(strings.Split(strings.TrimSpace(string(raw)), "\n")); lines != 1 {
				t.Fatalf("receipt has %d lines, want 1", lines)
			}
		})
	}
}

// Guard B's regression risk, BEAAA-24480 acceptance 3, and the reason
// ordered_batch_hashes[0] is not compared. Two v2 envelopes are built over the
// same events, the same batch_id and the same predecessor, one second apart. The
// emitted batch identity moves because the v2 chain body commits to issued_at,
// while every compared field is equal. This must still dedupe.
// Negative control: add ordered_batch_hashes[0] to divergentFields.
// Observed: "a benign v2 replay across a second boundary was refused:
// reconciliation divergence under idempotency key sha256:3a377f86...:
// ordered_batch_hashes[0] prior=sha256:5b5bf1d2... candidate=sha256:280d4845..."
// at receipt_test.go:390. That is the false alarm this exclusion exists to
// prevent, observed rather than argued.
func TestAppendAcceptsAV2ReplayWhoseBatchIdentityMovedAcrossASecond(t *testing.T) {
	privateKey := ed25519.NewKeyFromSeed(bytes.Repeat([]byte{7}, ed25519.SeedSize))
	events := []schemav1.Event{
		schemav1.NewEvent("evt-1", schemav1.EventToolCall, "2026-08-19T07:00:00Z", schemav1.SourceNumbat, map[string]any{"tool": "lookup", "billable": true}),
	}
	issued := time.Date(2026, 8, 19, 7, 0, 0, 0, time.UTC)
	first, err := egress.NewSignedChainedBatch("t", "a", "b1", "key-1", egress.GenesisBatchHash, issued, events, privateKey)
	if err != nil {
		t.Fatal(err)
	}
	second, err := egress.NewSignedChainedBatch("t", "a", "b1", "key-1", egress.GenesisBatchHash, issued.Add(time.Second), events, privateKey)
	if err != nil {
		t.Fatal(err)
	}
	// Without these two the test would pass whether or not the identity moved,
	// which would make it agree with the code rather than constrain it.
	if first.BatchHash == second.BatchHash {
		t.Fatal("the v2 batch identity did not move across a second boundary; this test is not exercising the case it exists for")
	}
	if first.BatchSHA256 != second.BatchSHA256 {
		t.Fatalf("the event digest moved across a second boundary: %s vs %s", first.BatchSHA256, second.BatchSHA256)
	}

	path := t.TempDir() + "/r.ndjson"
	base := Receipt{
		TenantID: "t", AgentID: "a", DeploymentID: "d",
		SourceCount: 1, EmittedCount: 1, NonBillableCount: 0,
		WireVersion: egress.WireVersionV2, EventTotals: map[string]int{"tool_call": 1},
		IdempotencyKey: IdempotencyKey("t", "a", "d", egress.WireVersionV2, "b1", egress.GenesisBatchHash, first.BatchSHA256),
		StartedAt:      issued.Format(time.RFC3339Nano),
		CompletedAt:    issued.Add(120 * time.Millisecond).Format(time.RFC3339Nano),
	}
	atT0 := base
	atT0.OrderedBatchHashes = []string{first.BatchHash}
	if _, replayed, err := Append(path, atT0); err != nil || replayed {
		t.Fatalf("first append: err=%v replayed=%v", err, replayed)
	}
	atT1 := base
	atT1.OrderedBatchHashes = []string{second.BatchHash}
	atT1.StartedAt = issued.Add(time.Second).Format(time.RFC3339Nano)
	atT1.CompletedAt = issued.Add(time.Second + 97*time.Millisecond).Format(time.RFC3339Nano)
	got, replayed, err := Append(path, atT1)
	if err != nil {
		t.Fatalf("a benign v2 replay across a second boundary was refused: %v", err)
	}
	if !replayed || got.ReplayOutcome != "idempotent_replay" {
		t.Fatalf("replay not deduped: replayed=%v receipt=%#v", replayed, got)
	}
	if got.OrderedBatchHashes[0] != first.BatchHash {
		t.Fatalf("replay returned the wrong prior identity: %v", got.OrderedBatchHashes)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if lines := len(strings.Split(strings.TrimSpace(string(raw)), "\n")); lines != 1 {
		t.Fatalf("receipt has %d lines, want 1", lines)
	}
}

// Guard B's second regression risk, BEAAA-24480 acceptance 4. A collector rebuild
// between two replays of the same spool is routine and must not fail closed.
// Negative control: add collector_version to divergentFields.
// Observed: "a replay from a rebuilt collector was refused: reconciliation
// divergence under idempotency key sha256:ea8f3c90...: collector_version
// prior=are-collector@76da84e+BEAAA-23693
// candidate=are-collector@910c8e6+BEAAA-24480" at receipt_test.go:431.
func TestAppendAcceptsAReplayWhoseBuildPinsMoved(t *testing.T) {
	path := t.TempDir() + "/r.ndjson"
	base := Receipt{
		TenantID: "t", AgentID: "a", DeploymentID: "d",
		SourceCount: 2, EmittedCount: 2, NonBillableCount: 1,
		OrderedBatchHashes: []string{"sha256:identity"}, WireVersion: "post-bind-egress-wire-v1",
		IdempotencyKey:   IdempotencyKey("t", "a", "d", "post-bind-egress-wire-v1", "b1", "", "sha256:events"),
		EventTotals:      map[string]int{"tool_call": 2},
		CollectorVersion: "are-collector@76da84e+BEAAA-23693",
	}
	if _, replayed, err := Append(path, base); err != nil || replayed {
		t.Fatalf("first append: err=%v replayed=%v", err, replayed)
	}
	rebuilt := base
	rebuilt.CollectorVersion = "are-collector@910c8e6+BEAAA-24480"
	got, replayed, err := Append(path, rebuilt)
	if err != nil {
		t.Fatalf("a replay from a rebuilt collector was refused: %v", err)
	}
	if !replayed || got.ReplayOutcome != "idempotent_replay" {
		t.Fatalf("replay not deduped: replayed=%v receipt=%#v", replayed, got)
	}
	if got.CollectorVersion != "are-collector@76da84e+BEAAA-23693" {
		t.Fatalf("replay did not return the prior row: %#v", got)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if lines := len(strings.Split(strings.TrimSpace(string(raw)), "\n")); lines != 1 {
		t.Fatalf("receipt has %d lines, want 1", lines)
	}
}
