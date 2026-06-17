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

func NewSignedBatch(customerID, agentID, batchID string, issuedAt time.Time, events []schemav1.Event, privateKey ed25519.PrivateKey) (Batch, error) {
	if customerID == "" || agentID == "" || batchID == "" {
		return Batch{}, fmt.Errorf("customer_id, agent, and batch_id are required")
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
	jwt, err := signJWT(claims, privateKey)
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

func ComputeBatchSHA256(events []schemav1.Event) (string, error) {
	digests := make([]string, 0, len(events))
	for _, event := range events {
		canonical, err := schemav1.CanonicalJSON(event)
		if err != nil {
			return "", err
		}
		sum := sha256.Sum256(canonical)
		digests = append(digests, hex.EncodeToString(sum[:]))
	}
	joined := strings.Join(digests, "\n")
	sum := sha256.Sum256([]byte(joined))
	return hex.EncodeToString(sum[:]), nil
}

func signJWT(claims map[string]any, privateKey ed25519.PrivateKey) (string, error) {
	header := map[string]any{"alg": "EdDSA", "typ": "JWT"}
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
