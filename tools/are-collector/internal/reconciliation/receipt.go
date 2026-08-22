package reconciliation

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
)

// ReceiptVersion is bumped to v3 with the addition of idempotency_key. v1 rows
// record only ordered_batch_hashes; v2 added wire_version. Neither could carry a
// dedupe key that is both stable across time and distinct per batch, because
// ordered_batch_hashes[0] holds the emitted batch identity, and on the v2 wire
// that identity commits to issued_at.
const ReceiptVersion = "numbat-reconciliation-receipt-v3"

// IdempotencyKey is the time-independent identity of a collection run: the same
// spool re-collected under the same pins produces the same key, while any batch
// that legitimately differs — a new batch_id, a different predecessor, different
// events — produces a different one. It is deliberately NOT the emitted batch
// hash: on the v2 wire that hash commits to issued_at, so it changes on every
// replay, and on the v1 wire it is the event digest alone, so two distinct
// batches over the same events collide.
func IdempotencyKey(tenantID, agentID, deploymentID, wireVersion, batchID, prevBatchHash, batchSHA256 string) string {
	fields := []string{tenantID, agentID, deploymentID, wireVersion, batchID, prevBatchHash, batchSHA256}
	// Length-prefixed and self-delimiting: each field is written as <byte
	// length>:<field> with no separator between fields, so the preimage parses
	// back to exactly one tuple whatever the fields contain. An escaped-separator
	// encoding is not enough here — escaping "|" as "||" and joining on "|" makes
	// ("tenant|", "agent") and ("tenant", "|agent") share the preimage
	// "tenant|||agent", which would silently merge two tenants onto one receipt row.
	var preimage strings.Builder
	for _, field := range fields {
		fmt.Fprintf(&preimage, "%d:%s", len(field), field)
	}
	sum := sha256.Sum256([]byte(preimage.String()))
	return "sha256:" + hex.EncodeToString(sum[:])
}

type Receipt struct {
	ReceiptVersion     string         `json:"receipt_version"`
	TenantID           string         `json:"tenant_id"`
	AgentID            string         `json:"agent_id"`
	DeploymentID       string         `json:"deployment_id"`
	SourceCount        int            `json:"source_count"`
	EmittedCount       int            `json:"emitted_count"`
	QuarantineCount    int            `json:"quarantine_count"`
	NonBillableCount   int            `json:"non_billable_count"`
	OrderedBatchHashes []string       `json:"ordered_batch_hashes"`
	WireVersion        string         `json:"wire_version"`
	IdempotencyKey     string         `json:"idempotency_key"`
	EventTotals        map[string]int `json:"event_totals"`
	ReplayOutcome      string         `json:"replay_outcome"`
	CollectorVersion   string         `json:"collector_version"`
	ScannerVersion     string         `json:"scanner_version"`
	CorpusVersion      string         `json:"corpus_version"`
	ProbeSetVersion    string         `json:"probe_set_version"`
	RubricPointID      string         `json:"rubric_point_id"`
	NumbatVersion      string         `json:"numbat_version"`
	NumbatCommit       string         `json:"numbat_commit"`
	NumbatSchema       string         `json:"numbat_schema"`
	MappingVersion     string         `json:"mapping_version"`
	StartedAt          string         `json:"started_at"`
	CompletedAt        string         `json:"completed_at"`
}

// Append writes one canonical receipt per batch hash. A replay is reported to
// the caller without appending a duplicate line.
func Append(path string, receipt Receipt) (Receipt, bool, error) {
	if path == "" || len(receipt.OrderedBatchHashes) != 1 {
		return Receipt{}, false, fmt.Errorf("receipt path and exactly one batch hash are required")
	}
	if receipt.WireVersion == "" {
		return Receipt{}, false, fmt.Errorf("receipt wire_version is required")
	}
	if receipt.IdempotencyKey == "" {
		return Receipt{}, false, fmt.Errorf("receipt idempotency_key is required")
	}
	if receipt.SourceCount != receipt.EmittedCount+receipt.QuarantineCount {
		return Receipt{}, false, fmt.Errorf("reconciliation mismatch: source=%d emitted=%d quarantine=%d", receipt.SourceCount, receipt.EmittedCount, receipt.QuarantineCount)
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR|os.O_APPEND, 0o600)
	if err != nil {
		return Receipt{}, false, err
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	var matched *Receipt
	for scanner.Scan() {
		var prior Receipt
		if json.Unmarshal(scanner.Bytes(), &prior) != nil {
			continue
		}
		// Guard A. A receipt file written by an earlier schema cannot be deduped
		// against: v2 and older rows carry no idempotency_key, so every one of
		// them reads as non-matching and this collector would silently re-emit a
		// batch the file already records. Refuse the file and name the operator
		// action. The alternative — matching legacy rows on
		// ordered_batch_hashes[0] — would reintroduce exactly the key semantics
		// the idempotency key exists to remove. Refusing the file does the
		// opposite: the seam between the two schemas becomes explicit.
		if prior.ReceiptVersion != ReceiptVersion {
			return Receipt{}, false, fmt.Errorf("receipt file %s holds a %q row but this collector writes %q: rotate the receipt file (archive it and point -reconciliation-receipt at a new file) before collecting", path, prior.ReceiptVersion, ReceiptVersion)
		}
		if matched == nil && prior.IdempotencyKey == receipt.IdempotencyKey {
			row := prior
			matched = &row
		}
	}
	if err := scanner.Err(); err != nil {
		return Receipt{}, false, err
	}
	if matched != nil {
		// Guard B. The same key over different reconciliation numbers is an
		// integrity signal, not a replay: the key commits to the tenant, the
		// agent, the deployment, the wire version, the batch id, the predecessor
		// and the event digest, so equal keys mean the counting moved underneath
		// a fixed input.
		if divergent := divergentFields(*matched, receipt); len(divergent) > 0 {
			return Receipt{}, false, fmt.Errorf("reconciliation divergence under idempotency key %s: %s", receipt.IdempotencyKey, strings.Join(divergent, "; "))
		}
		matched.ReplayOutcome = "idempotent_replay"
		return *matched, true, nil
	}
	receipt.ReceiptVersion = ReceiptVersion
	receipt.ReplayOutcome = "accepted"
	raw, err := json.Marshal(receipt)
	if err != nil {
		return Receipt{}, false, err
	}
	if _, err := file.Write(append(raw, '\n')); err != nil {
		return Receipt{}, false, err
	}
	if err := file.Sync(); err != nil {
		return Receipt{}, false, err
	}
	return receipt, false, nil
}

// divergentFields reports the reconciliation numbers that differ between a prior
// receipt row and a candidate carrying the same idempotency key.
//
// Three groups of fields are deliberately excluded, and the exclusions are
// load-bearing rather than stylistic:
//
//   - ordered_batch_hashes[0] — the emitted batch identity. On the v2 wire it
//     commits to issued_at, so it moves legitimately between two replays of the
//     same spool. Comparing it would fail closed on precisely the benign replay
//     that splitting the reconciliation reference from the dedupe key exists to
//     accept. TestAppendAcceptsAV2ReplayWhoseBatchIdentityMovedAcrossASecond
//     pins that.
//   - started_at / completed_at — RFC3339Nano wall clock; they always differ.
//   - the build-version pins (collector_version, scanner_version, corpus_version,
//     probe_set_version, numbat_version, numbat_commit, numbat_schema,
//     mapping_version). A collector rebuild between two replays of the same spool
//     is routine, and failing closed there is a false alarm on a benign replay. A
//     mapping-table change that actually moves the reconciliation numbers is
//     caught by the counts below — non_billable_count in particular.
func divergentFields(prior, candidate Receipt) []string {
	var divergent []string
	for _, counter := range []struct {
		name             string
		before, proposed int
	}{
		{"source_count", prior.SourceCount, candidate.SourceCount},
		{"emitted_count", prior.EmittedCount, candidate.EmittedCount},
		{"quarantine_count", prior.QuarantineCount, candidate.QuarantineCount},
		{"non_billable_count", prior.NonBillableCount, candidate.NonBillableCount},
	} {
		if counter.before != counter.proposed {
			divergent = append(divergent, fmt.Sprintf("%s prior=%d candidate=%d", counter.name, counter.before, counter.proposed))
		}
	}
	if !equalTotals(prior.EventTotals, candidate.EventTotals) {
		divergent = append(divergent, fmt.Sprintf("event_totals prior=%s candidate=%s", formatTotals(prior.EventTotals), formatTotals(candidate.EventTotals)))
	}
	return divergent
}

// equalTotals compares by content, so an absent map and an empty one are equal. A
// run that emitted no events serializes event_totals as {} and reads back as an
// empty map, while a caller that never set the field passes nil. Neither is a
// divergence, and reflect.DeepEqual would call it one.
func equalTotals(prior, candidate map[string]int) bool {
	if len(prior) != len(candidate) {
		return false
	}
	for eventType, count := range prior {
		if other, ok := candidate[eventType]; !ok || other != count {
			return false
		}
	}
	return true
}

// formatTotals renders event totals in key order, so the divergence message is
// the same on every run rather than following Go's map iteration.
func formatTotals(totals map[string]int) string {
	eventTypes := make([]string, 0, len(totals))
	for eventType := range totals {
		eventTypes = append(eventTypes, eventType)
	}
	sort.Strings(eventTypes)
	rendered := make([]string, 0, len(eventTypes))
	for _, eventType := range eventTypes {
		rendered = append(rendered, fmt.Sprintf("%s=%d", eventType, totals[eventType]))
	}
	return "{" + strings.Join(rendered, ",") + "}"
}
