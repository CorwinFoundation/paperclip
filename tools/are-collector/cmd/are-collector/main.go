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
	"backbond/are-collector/internal/redaction"
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
	sink, err := adapter.AdapterFromFixture(*source, stdin)
	if err != nil {
		return err
	}
	events, err := collector.Pipeline{Adapter: sink, Policy: policy}.Collect(context.Background())
	if err != nil {
		return err
	}
	encoder := json.NewEncoder(stdout)
	encoder.SetEscapeHTML(false)
	switch *wireVersion {
	case "post-bind-egress-wire-v1":
		batch, err := egress.NewSignedBatch(*customerID, *agentID, *batchID, *keyID, time.Now().UTC(), events, privateKey)
		if err != nil {
			return err
		}
		return encoder.Encode(batch)
	case egress.WireVersionV2:
		batch, err := egress.NewSignedChainedBatch(*customerID, *agentID, *batchID, *keyID, *prevBatchHash, time.Now().UTC(), events, privateKey)
		if err != nil {
			return err
		}
		return encoder.Encode(batch)
	default:
		return fmt.Errorf("unsupported wire-version %q", *wireVersion)
	}
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
