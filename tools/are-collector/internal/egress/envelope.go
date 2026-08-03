package egress

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"backbond/are-collector/internal/schemav1"
)

type Batch struct {
	CustomerID    string           `json:"customer_id"`
	AgentID       string           `json:"agent"`
	SchemaVersion string           `json:"schema_version"`
	BatchID       string           `json:"batch_id"`
	BatchSHA256   string           `json:"batch_sha256"`
	EventCount    int              `json:"event_count"`
	IssuedAt      string           `json:"issued_at"`
	Events        []schemav1.Event `json:"events"`
	JWT           string           `json:"jwt"`
}

const (
	WireVersionV2    = "post-bind-egress-wire-v2"
	GenesisBatchHash = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
)

// ChainedBatch is the additive v2 wire envelope. Batch remains byte-locked v1.
type ChainedBatch struct {
	Batch
	WireVersion   string `json:"wire_version"`
	PrevBatchHash string `json:"prev_batch_hash"`
	BatchHash     string `json:"batch_hash"`
}

func NewSignedBatch(customerID, agentID, batchID, keyID string, issuedAt time.Time, events []schemav1.Event, privateKey ed25519.PrivateKey) (Batch, error) {
	if customerID == "" || agentID == "" || batchID == "" || keyID == "" {
		return Batch{}, fmt.Errorf("customer_id, agent, batch_id, and key_id are required")
	}
	if len(privateKey) != ed25519.PrivateKeySize {
		return Batch{}, fmt.Errorf("ed25519 private key is required")
	}
	for idx, event := range events {
		if err := schemav1.ValidateEvent(event); err != nil {
			return Batch{}, fmt.Errorf("event[%d]: %w", idx, err)
		}
	}
	batchSHA, err := ComputeBatchSHA256(events)
	if err != nil {
		return Batch{}, err
	}
	issued := issuedAt.UTC().Format(time.RFC3339)
	claims := map[string]any{
		"iss":            "are-collector",
		"sub":            customerID,
		"agent":          agentID,
		"customer_id":    customerID,
		"schema_version": schemav1.Version,
		"batch_id":       batchID,
		"batch_sha256":   "sha256:" + batchSHA,
		"event_count":    len(events),
		"issued_at":      issued,
		"exp":            issuedAt.UTC().Add(10 * time.Minute).Unix(),
	}
	jwt, err := signJWT(claims, privateKey, keyID)
	if err != nil {
		return Batch{}, err
	}
	return Batch{
		CustomerID:    customerID,
		AgentID:       agentID,
		SchemaVersion: schemav1.Version,
		BatchID:       batchID,
		BatchSHA256:   "sha256:" + batchSHA,
		EventCount:    len(events),
		IssuedAt:      issued,
		Events:        events,
		JWT:           jwt,
	}, nil
}

// NewSignedChainedBatch emits v2 through the same event path as v1 while binding
// the canonical predecessor hash into the signed claims.
func NewSignedChainedBatch(customerID, agentID, batchID, keyID, prevBatchHash string, issuedAt time.Time, events []schemav1.Event, privateKey ed25519.PrivateKey) (ChainedBatch, error) {
	if !validSHA256Ref(prevBatchHash) {
		return ChainedBatch{}, fmt.Errorf("prev_batch_hash must use closed sha256:<64 lowercase hex> format")
	}
	base, err := NewSignedBatch(customerID, agentID, batchID, keyID, issuedAt, events, privateKey)
	if err != nil {
		return ChainedBatch{}, err
	}
	chainBody := map[string]any{
		"customer_id": customerID, "agent": agentID, "schema_version": base.SchemaVersion,
		"wire_version": WireVersionV2, "batch_id": batchID, "batch_sha256": base.BatchSHA256,
		"prev_batch_hash": prevBatchHash, "event_count": len(events), "issued_at": base.IssuedAt,
	}
	canonical, err := schemav1.CanonicalJSON(chainBody)
	if err != nil {
		return ChainedBatch{}, err
	}
	digest := sha256.Sum256(canonical)
	batchHash := "sha256:" + hex.EncodeToString(digest[:])
	claims := map[string]any{
		"iss": "are-collector", "sub": customerID, "agent": agentID, "customer_id": customerID,
		"schema_version": base.SchemaVersion, "wire_version": WireVersionV2,
		"batch_id": batchID, "batch_sha256": base.BatchSHA256, "prev_batch_hash": prevBatchHash, "batch_hash": batchHash,
		"event_count": len(events), "issued_at": base.IssuedAt,
		"exp": issuedAt.UTC().Add(10 * time.Minute).Unix(),
	}
	base.JWT, err = signJWT(claims, privateKey, keyID)
	if err != nil {
		return ChainedBatch{}, err
	}
	return ChainedBatch{Batch: base, WireVersion: WireVersionV2, PrevBatchHash: prevBatchHash, BatchHash: batchHash}, nil
}

func validSHA256Ref(value string) bool {
	if len(value) != len("sha256:")+sha256.Size*2 || value[:len("sha256:")] != "sha256:" {
		return false
	}
	_, err := hex.DecodeString(value[len("sha256:"):])
	return err == nil && value == strings.ToLower(value)
}

func ComputeBatchSHA256(events []schemav1.Event) (string, error) {
	raw, err := json.Marshal(events)
	if err != nil {
		return "", err
	}
	var normalized any
	if err := json.Unmarshal(raw, &normalized); err != nil {
		return "", err
	}
	canonical, err := schemav1.CanonicalJSON(normalized)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(canonical)
	return hex.EncodeToString(sum[:]), nil
}

func signJWT(claims map[string]any, privateKey ed25519.PrivateKey, keyID string) (string, error) {
	if keyID == "" {
		return "", fmt.Errorf("jwt key id is required")
	}
	header := map[string]any{"alg": "EdDSA", "typ": "JWT", "kid": keyID}
	headerJSON, err := json.Marshal(header)
	if err != nil {
		return "", err
	}
	claimsJSON, err := json.Marshal(claims)
	if err != nil {
		return "", err
	}
	head := b64url(headerJSON)
	body := b64url(claimsJSON)
	payload := head + "." + body
	signature := ed25519.Sign(privateKey, []byte(payload))
	return payload + "." + b64url(signature), nil
}

func b64url(value []byte) string {
	return base64.RawURLEncoding.EncodeToString(value)
}
