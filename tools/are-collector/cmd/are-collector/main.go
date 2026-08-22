package main

import (
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"time"

	"backbond/are-collector/internal/adapter"
	"backbond/are-collector/internal/collector"
	"backbond/are-collector/internal/egress"
	"backbond/are-collector/internal/reconciliation"
	"backbond/are-collector/internal/redaction"
	"backbond/are-collector/internal/schemav1"
	numbatsource "backbond/are-collector/internal/source"
)

func main() {
	if err := run(os.Args[1:], os.Stdin, os.Stdout); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run(args []string, stdin io.Reader, stdout io.Writer) error {
	flags := flag.NewFlagSet("are-collector", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	mode := flags.String("mode", "", "collector mode; currently only fixture-smoke is enabled")
	source := flags.String("source", "", "one of datadog, splunk, cloudwatch, otel")
	customerID := flags.String("customer-id", "", "BackBond customer id")
	agentID := flags.String("agent-id", "", "BackBond agent id")
	batchID := flags.String("batch-id", "", "batch id")
	wireVersion := flags.String("wire-version", "post-bind-egress-wire-v1", "egress wire version")
	prevBatchHash := flags.String("prev-batch-hash", "", "v2 predecessor batch_hash")
	keyID := flags.String("key-id", "", "per-bind Ed25519 public key id")
	policyJSON := flags.String("policy-json", "{}", "JSON object mapping payload paths to redaction classes")
	privateKeyB64 := flags.String("ed25519-private-key-b64", "", "raw base64 Ed25519 private key")
	receiptPath := flags.String("reconciliation-receipt", "", "append-only local Numbat receipt path")
	deploymentID := flags.String("deployment-id", "", "pinned deployment id")
	numbatCommit := flags.String("numbat-commit", "", "Numbat source commit pin")
	numbatSchema := flags.String("numbat-schema", "", "Numbat schema pin")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if *mode == "" || *source == "" || *customerID == "" || *agentID == "" || *batchID == "" || *keyID == "" {
		return fmt.Errorf("mode, source, customer-id, agent-id, batch-id, and key-id are required")
	}
	if *mode != "fixture-smoke" {
		return fmt.Errorf("collector mode %q is not wired; only fixture-smoke is enabled in this image", *mode)
	}
	if *privateKeyB64 == "" {
		return fmt.Errorf("ed25519-private-key-b64 is required")
	}
	policy, err := parsePolicy(*policyJSON)
	if err != nil {
		return err
	}
	privateKey, err := parsePrivateKey(*privateKeyB64)
	if err != nil {
		return err
	}
	var events []schemav1.Event
	started := time.Now().UTC()
	if *source == "numbat" {
		if *receiptPath == "" || *deploymentID == "" {
			return fmt.Errorf("numbat requires reconciliation-receipt and deployment-id")
		}
		if *numbatCommit != numbatsource.NumbatCommit {
			return fmt.Errorf("numbat commit pin mismatch: got %q, require %q", *numbatCommit, numbatsource.NumbatCommit)
		}
		if *numbatSchema != numbatsource.NumbatSchemaVersion {
			return fmt.Errorf("numbat schema pin mismatch: got %q, require %q", *numbatSchema, numbatsource.NumbatSchemaVersion)
		}
		driver, driverErr := numbatsource.NewNumbatSourceDriver(stdin)
		if driverErr != nil {
			return driverErr
		}
		events, err = (collector.Pipeline{}).CollectDriver(context.Background(), driver, 0)
	} else {
		sink, adapterErr := adapter.AdapterFromFixture(*source, stdin)
		if adapterErr != nil {
			return adapterErr
		}
		events, err = collector.Pipeline{Adapter: sink, Policy: policy}.Collect(context.Background())
	}
	if err != nil {
		return err
	}
	encoder := json.NewEncoder(stdout)
	encoder.SetEscapeHTML(false)
	var batch any
	// batchIdentity is the hash the receipt binds: the v1 event digest on the v1
	// wire, and the v2 chain identity (which commits to wire_version and
	// prev_batch_hash) on the v2 wire. Binding batch_sha256 on v2 would make the
	// receipt unreconcilable against a chained stream and would dedupe two
	// distinct-predecessor batches into one row.
	var batchIdentity string
	var selectedWire string
	// batchDigest is the time-independent event digest. It feeds the receipt's
	// idempotency key; batchIdentity is what the receipt reports as the emitted
	// batch. On v2 those must be different values: the identity commits to
	// issued_at, so it cannot also serve as a replay key.
	var batchDigest string
	switch *wireVersion {
	case "post-bind-egress-wire-v1":
		signed, err := egress.NewSignedBatch(*customerID, *agentID, *batchID, *keyID, time.Now().UTC(), events, privateKey)
		if err != nil {
			return err
		}
		batch, batchIdentity, selectedWire, batchDigest = signed, signed.BatchSHA256, "post-bind-egress-wire-v1", signed.BatchSHA256
	case egress.WireVersionV2:
		signed, err := egress.NewSignedChainedBatch(*customerID, *agentID, *batchID, *keyID, *prevBatchHash, time.Now().UTC(), events, privateKey)
		if err != nil {
			return err
		}
		batch, batchIdentity, selectedWire, batchDigest = signed, signed.BatchHash, egress.WireVersionV2, signed.BatchSHA256
	default:
		return fmt.Errorf("unsupported wire-version %q", *wireVersion)
	}
	if *source == "numbat" {
		totals := map[string]int{}
		nonBillable := 0
		for _, event := range events {
			totals[string(event.EventType)]++
			if billable, _ := event.Payload["billable"].(bool); !billable {
				nonBillable++
			}
		}
		receipt, replay, receiptErr := reconciliation.Append(*receiptPath, reconciliation.Receipt{
			TenantID: *customerID, AgentID: *agentID, DeploymentID: *deploymentID,
			SourceCount: len(events), EmittedCount: len(events), QuarantineCount: 0, NonBillableCount: nonBillable,
			OrderedBatchHashes: []string{batchIdentity}, WireVersion: selectedWire, EventTotals: totals,
			IdempotencyKey:   reconciliation.IdempotencyKey(*customerID, *agentID, *deploymentID, selectedWire, *batchID, *prevBatchHash, batchDigest),
			CollectorVersion: "are-collector@76da84e+BEAAA-23693", ScannerVersion: "0.5.0", CorpusVersion: "0.1.0",
			ProbeSetVersion: "monitor-only-numbat-v0.1", RubricPointID: "none-exercised", NumbatVersion: numbatsource.NumbatVersion,
			NumbatCommit: numbatsource.NumbatCommit, NumbatSchema: numbatsource.NumbatSchemaVersion, MappingVersion: numbatsource.NumbatMappingVersion,
			StartedAt: started.Format(time.RFC3339Nano), CompletedAt: time.Now().UTC().Format(time.RFC3339Nano),
		})
		if receiptErr != nil {
			return receiptErr
		}
		if replay {
			return encoder.Encode(map[string]any{"batch": nil, "reconciliation_receipt": receipt})
		}
		return encoder.Encode(map[string]any{"batch": batch, "reconciliation_receipt": receipt})
	}
	return encoder.Encode(batch)
}

func parsePolicy(raw string) (redaction.Policy, error) {
	values := map[string]string{}
	if err := json.Unmarshal([]byte(raw), &values); err != nil {
		return nil, fmt.Errorf("policy-json must be an object: %w", err)
	}
	policy := redaction.Policy{}
	for path, value := range values {
		switch redaction.Class(value) {
		case redaction.Clear, redaction.Hash, redaction.RedactOnReceipt, redaction.Drop:
			policy[path] = redaction.Class(value)
		default:
			return nil, fmt.Errorf("unsupported redaction class for %s: %q", path, value)
		}
	}
	return policy, nil
}

func parsePrivateKey(raw string) (ed25519.PrivateKey, error) {
	if raw == "" {
		return nil, fmt.Errorf("ed25519 private key is required")
	}
	decoded, err := base64.StdEncoding.DecodeString(raw)
	if err != nil {
		return nil, fmt.Errorf("invalid ed25519 private key base64: %w", err)
	}
	if len(decoded) != ed25519.PrivateKeySize {
		return nil, fmt.Errorf("ed25519 private key must be %d bytes", ed25519.PrivateKeySize)
	}
	return ed25519.PrivateKey(decoded), nil
}
