# Release Evidence Ingress

Scoped release evidence ingress accepts immutable release-candidate evidence from GitHub Actions without exposing a general Paperclip API key to the workflow.

## Exchange

`POST /api/release-evidence/v1/exchange`

Authentication is a GitHub Actions OIDC bearer token. The token must have audience `paperclip-release-evidence` and must match an active `release_evidence_grants` row:

- repository and optional repository id
- triggering ref and triggering SHA
- workflow ref and workflow SHA
- optional job workflow ref and job workflow SHA for reusable workflows
- candidate source SHA from the request body
- event name, excluding pull request events
- allowed issue id
- sequence and environment
- maximum upload bytes

GitHub `workflow_ref` is compared as the ref path claim, for example `owner/repo/.github/workflows/file.yml@refs/heads/qa-branch`. GitHub `workflow_sha` is compared as its own immutable claim. The workflow trigger `sha` is recorded separately from `sourceSha`; standalone QA workflows commonly start from a QA ref and then check out the candidate source SHA during the job. `job_workflow_ref` and `job_workflow_sha` are required only when the grant was provisioned for a reusable workflow.

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

## Grant Provisioning

`POST /api/release-evidence/v1/grants`

Authenticated board/admin callers can provision a single active release-evidence grant for one issue. The endpoint expires or revokes older active grants for that issue scope, writes a redacted audit event, and returns only the opaque grant id:

```json
{
  "companyId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  "repository": "CorwinFoundation/are-scanner",
  "repositoryId": "123456",
  "workflowRef": "CorwinFoundation/are-scanner/.github/workflows/scanner-edge-qa-evidence.yml@refs/heads/beaaa-16889-qa-evidence",
  "workflowSha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "triggerRef": "refs/heads/beaaa-16889-qa-evidence",
  "issueId": "11111111-1111-4111-8111-111111111111",
  "sourceSha": "d2d53b9c65668ff44a2e3fc9c3dd3c23a978a76c",
  "sequence": 20,
  "environment": "qa-evidence",
  "maxUploadBytes": 268435456,
  "allowedEventName": "workflow_dispatch",
  "expiresAt": "2026-07-22T18:40:00.000Z"
}
```

Use `"dryRun": true` for preflight. Expiry is capped at 24 hours.

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

This ingress only creates source-side release evidence. Shared activation, workflow cutover, deployment, or promotion still requires QA approval for the exact candidate SHA and any required Founder sign-off.
