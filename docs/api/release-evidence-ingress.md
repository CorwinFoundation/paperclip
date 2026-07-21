# Release Evidence Ingress

Scoped release evidence ingress accepts immutable release-candidate evidence from GitHub Actions without exposing a general Paperclip API key to the workflow.

## Exchange

`POST /api/release-evidence/v1/exchange`

Authentication is a GitHub Actions OIDC bearer token. The token must have audience `paperclip-release-evidence` and must match an active `release_evidence_grants` row:

- repository and optional repository id
- workflow ref and job workflow ref
- source SHA
- event name, excluding pull request events
- allowed issue id
- sequence and environment
- workflow SHA pinned in the workflow ref
- maximum upload bytes

The request body provides the release coordinates and a client nonce:

```json
{
  "grantId": "22222222-2222-4222-8222-222222222222",
  "issueId": "11111111-1111-4111-8111-111111111111",
  "sourceSha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "workflowSha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "sequence": 20,
  "environment": "production",
  "imageDigest": "ghcr.io/corwinfoundation/are-scanner@sha256:...",
  "bundleSha256": "sha256:...",
  "bundleBytes": 12345,
  "clientNonce": "workflow-generated-random-value"
}
```

A valid exchange returns a five-minute upload capability:

```json
{
  "sessionId": "33333333-3333-4333-8333-333333333333",
  "capability": "pcrel_...",
  "uploadUrl": "/api/release-evidence/v1/sessions/33333333-3333-4333-8333-333333333333/attachment",
  "expiresAt": "2026-07-21T12:05:00.000Z"
}
```

## Upload

`POST /api/release-evidence/v1/sessions/:sessionId/attachment`

Send multipart form data with one `file` field and header:

```text
X-Paperclip-Release-Evidence-Capability: pcrel_...
```

The server computes SHA-256 and byte size from the uploaded bytes before storage. Upload is exactly-once per session: concurrent consumers must atomically claim the issued session before creating asset or issue-attachment rows. Byte-identical replay returns the existing attachment.

## Audit

Exchange and upload denials write redacted audit events. OIDC claims are bounded to GitHub claim fields, and request details store `clientNonceHash` rather than the raw client nonce.

## Verification

Focused server verification:

```sh
pnpm vitest run server/src/services/release-evidence-ingress.test.ts server/src/__tests__/release-evidence-routes.test.ts
```

## Promotion Gate

This ingress only creates source-side release evidence. Shared activation, grant provisioning, workflow cutover, deployment, or promotion still requires QA approval for the exact candidate SHA and any required Founder sign-off.
