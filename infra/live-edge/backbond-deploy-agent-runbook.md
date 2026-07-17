---
artifact_type: other
owner: scanner_engineer
persona_audience: internal
phase: 3
canonical_terms_only: true
focus_claim: "This runbook replaces ad hoc BackBond Scanner production pushes with an approved, on-box deploy lease path that preserves release evidence."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-13989", "BEAAA-13363"]
proof_points_cited: ["ADR-0060 revision feff049c-117a-4fa2-a8c5-3aeb142509bd", "Founder confirmation 3fb03d77-c910-47ca-af6d-9562259f8001"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# BackBond Deploy Agent Runbook

BLUF: `scripts/backbond_deploy_agent.py` is the srv1749248-side pull agent for ADR-0060. It polls Paperclip for an approved deploy lease, downloads only the candidate tarball named by the immutable release candidate, re-verifies digest/signature/commit/sequence/expiry/host/environment on box, activates atomically, runs acceptance, and posts the deploy record.

Scope: deploy plumbing only. This does not change BackBond Scanner scoring, probe corpus, judge model, evidence schema, or `are-scanner-api-v1@v1.0` output shape.

## Required Lease Fields

The Paperclip control plane returns one flat approved-lease object. The scanner pins the exact JSON shape in `tests/fixtures/paperclip-approved-lease-v1.json`; Paperclip pins the same fixture byte-for-byte. Required fields are:

- authorization/lease binding: `authorizationId`, `candidateId`, `environment`, `targetHost`, `sequence`, `expiresAt`, and `deployRecordReceiptPath`
- immutable candidate identity: `commitSha`, `imageDigest`, `approvalInteractionId`
- authorization-bound relay artifacts: `stagedArtifactPath`, `stagedArtifactSha256` (or `tarballSha256`), `stagedSignatureBundlePath`, and `stagedSignatureBundleSha256`

Live mode rejects original GitHub artifact URLs and requires both staged paths to be same-origin Paperclip `/api/` routes with no userinfo, query, or fragment. `PAPERCLIP_API_URL` must be an HTTPS origin; plain HTTP is accepted only for loopback tests. The receipt path is immutable per authorization and accepts idempotent retries using the Paperclip agent API key only; a one-time deploy token is never stored in the spool or rebound after rotation.

The deploy token scope is recorded as a scope string only. No token secret is written to the deploy record.

Default Paperclip routes:

- approved lease: `/api/release-candidates/approved-lease?authorizationId={authorization_id}`
- deploy record: `/api/release-candidates/deploy-records`

If the control plane deploys a different route shape, set `BACKBOND_DEPLOY_LEASE_PATH_TEMPLATE` and `BACKBOND_DEPLOY_RECORD_PATH` explicitly before activation. Do not edit the on-box script for route drift.

## On-Box Invocation

Live invocation is exclusively `systemctl start backbond-deploy-agent.service`. Do not construct an environment-prefixed Python command: all secrets and the dynamic authorization id enter through systemd credential files.

Dry-run mode accepts local JSON fixtures. It downloads and verifies candidate evidence only: it does not retry the receipt spool, construct the Docker/systemd activator, write deploy state, change the service environment, restart a unit, or post a deploy record. Local JSON is rejected unless `--dry-run` is present.

```bash
python3 scripts/backbond_deploy_agent.py \
  --candidate-json /tmp/candidate.json \
  --lease-json /tmp/lease.json \
  --state-dir /tmp/backbond-deploy-agent \
  --environment production \
  --host srv1749248 \
  --dry-run
```

## Fail-Closed Checks

Pre-activation rejection occurs before any service mutation when:

- candidate or lease is expired
- host or environment is not `srv1749248` / `production`
- lease and candidate `sequence`, `candidate_id`, or token scope diverge
- tarball SHA-256 differs from the Paperclip candidate
- signature verification fails
- the offline Docker archive has an unsafe member, wrong commit tag/label, invalid config or layer digest, non-`linux/amd64` platform, or a loaded image ID that differs from the verified archive config
- an earlier spool entry remains `activation_pending`

Post-activation acceptance failure triggers rollback to the previous version and posts a failed deploy record.

The deploy agent snapshots the current version, writes an `activation_pending` spool entry durably, and only then mutates Docker/systemd state. A crash that leaves `activation_pending` is never reported as terminal evidence and blocks later candidates. An operator must reconcile the live service/image against the pending record, replace it with a correctly bound terminal record, and rerun the service; never delete or relabel pending evidence without that inspection.

The archive is verified before `docker load`: its single tag and revision label must match the candidate commit, its config hash and every uncompressed layer hash must match the archive manifest, and Docker must report the same immutable image ID after loading. That image ID is written atomically to `/etc/backbond/scanner-edge.env`, the exact `EnvironmentFile` consumed by `backbond-scanner-live.service`. Existing comments and unrelated settings are preserved. Acceptance reads `/var/lib/are-scanner/scan_log.sqlite3` and binds pins to the exact scan id created by that acceptance run.

## Deploy Record

Every on-box record includes commit SHA, image digest, signature verification result, approval interaction id, deploy-token scope, on-box re-verification result, acceptance result, rollback status, activated service version, and operator/run identity.

For the first terminal post, the client sends the authorization id and one-time token in dedicated request headers. If the response is lost, the durable spool retries the authorization-bound `deployRecordReceiptPath` with the agent API key only; the server deduplicates the exact terminal receipt and rejects conflicting evidence. The token is never stored in metadata, spool content, argv, URLs, logs, or the unit environment.

Install the unit and provision its one-shot token as an encrypted systemd credential. The launcher reads it from `$CREDENTIALS_DIRECTORY/deploy-token`; systemd materializes that file under `/run` with restricted access.

```bash
sudo install -m 0644 infra/live-edge/backbond-deploy-agent.service /etc/systemd/system/
sudo install -m 0755 infra/live-edge/run-backbond-deploy-agent /opt/backbond/are-scanner/infra/live-edge/
sudo systemd-creds encrypt --name=deploy-token - /etc/credstore.encrypted/backbond-deploy-token
sudo systemd-creds encrypt --name=paperclip-api-key - /etc/credstore.encrypted/backbond-paperclip-api-key
sudo systemd-creds encrypt --name=acceptance-admin-token - /etc/credstore.encrypted/backbond-acceptance-admin-token
sudo install -d -m 0755 /etc/backbond
sudo install -d -m 0700 /var/lib/backbond-deploy-agent
sudo touch /etc/backbond/scanner-edge.env
sudo chmod 0600 /etc/backbond/scanner-edge.env
sudo install -m 0600 /dev/stdin /etc/backbond/deploy-agent.conf # contains only PAPERCLIP_API_URL=<approved URL>
sudo install -d -m 0700 /run/backbond-deploy-agent
sudo install -m 0600 /dev/stdin /run/backbond-deploy-agent/authorization-id
sudo systemctl daemon-reload
sudo systemctl start backbond-deploy-agent.service
```
