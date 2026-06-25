---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This connector package lets design partners run Backbond Scanner staging tasks inside their environment while keeping probe content and scoring logic off the shipped connector."
contracts_referenced: ["ARE scanner API v1", "scanner-implementation-supervision-v1", "connector-portal-api-v0.1"]
ticket_refs: ["BEAAA-4721", "BEAAA-4700", "BEAAA-4699"]
proof_points_cited: ["connector-portal-api-v0.1 §5.2 enrollment", "connector-portal-api-v0.1 §5.3 envelope schema", "connector-portal-api-v0.1 §5.4 signed redaction policy", "ARE scanner API v1.0 hard guarantees #1/#2/#3/#5"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# Backbond Connector v0.1.0

BLUF: this package builds the DP-side connector image for BEAAA-4721. It enrolls with the portal, long-polls for encrypted staging tasks, calls the DP-configured agent endpoint, applies a signed redaction policy, signs v0.1 envelopes, and posts them back to the portal. It does not contain corpus payloads, scoring logic, weight tables, or judge calls.

## Build

```bash
docker build --network=none -t ghcr.io/ifa/are-connector:0.1.0 tools/are-connector
```

The Dockerfile pins the base image by digest. Release packaging should use the same source tree and tag the resulting image as `ghcr.io/ifa/are-connector:0.1.0`.

## Enrollment

```bash
docker run --rm \
  -e PORTAL_BASE_URL=https://portal.example.invalid \
  -e DP_ORG_NAME=example-dp \
  -v connector-state:/var/lib/are-connector \
  ghcr.io/ifa/are-connector:0.1.0 \
  --enroll "$INVITE_TOKEN"
```

The connector generates an ephemeral X25519 keypair at process start. If TPM2 tooling is present, enrollment reports `key_attestation.type=tpm2`; otherwise it reports `software`. Software fallback never writes the X25519 private key to disk. The persisted config contains connector id, cert material, OIDC client credentials, CA chain, portal URL, signed policy, the envelope-signing key used for later scan runs, and the public half of the ephemeral X25519 key only.

## Run A Scan

```bash
docker run --rm \
  -e PORTAL_BASE_URL=https://portal.example.invalid \
  -e DP_AGENT_ENDPOINT=https://agent.example.invalid/staging \
  -v connector-state:/var/lib/are-connector \
  ghcr.io/ifa/are-connector:0.1.0 \
  --scan "$SCAN_ID"
```

The connector long-polls `GET /scans/{scan_id}/tasks/next` with 60-second hold semantics. It posts one signed envelope per task to `POST /scans/{scan_id}/envelopes`; submissions are idempotent by `(scan_id, task_id)`.

## DP Firewall Egress

Allow only these outbound destinations:

| Destination | Purpose |
|---|---|
| Portal API base URL | Enrollment, policy fetch, long-poll, envelope POST, scan completion |
| DP-configured agent endpoint | Staging endpoint under scan |

The connector has no public inbound surface. Optional local observability binds to `127.0.0.1` only when `--serve-local` is set: `GET /healthz` and `GET /scan-history`.

## Redaction And Envelope Rules

- The connector refuses policies without a valid portal signature.
- Full conversation bodies, task payloads, tool arguments, secrets, and credential-like strings are never placed in envelopes.
- Tool evidence is tool-name-only through `assertions.tool_called`.
- DP-agent call failure still produces a signed envelope with `assertions.response_received=false`, `error.class`, and no raw error detail.
- Envelopes are signed over RFC 8785-style canonical JSON excluding `signature` and `signature_chain`.

## Contract Pins

- `connector_version`: 0.1.0
- `envelope_version`: 0.1
- `redaction_policy_version`: 0.1
- `scanner_version`: 0.4.1
- `corpus_version`: 0.2.0
- `probe-set-version`: 1.2.0
- Contract anchor: ARE scanner API v1.0

N=5 calibration is owned by BEAAA-4700 Child 3. This package includes smoke tests for canonicalization, redaction, envelope signing, and egress allowlisting; the staging-endpoint N=5 gate must still pass before release.
