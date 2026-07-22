# Production release pipeline - Paperclip-approved deploy lease

Production releases use one path: the `scanner-edge-image` GitHub Actions
workflow creates a signed release candidate, Paperclip records the Founder
approval and issues a host-bound deploy lease, and the one-shot deploy agent on
`srv1749248` verifies and activates that candidate.

The retired `.github/workflows/cd.yml` approval-issue/operator-push workflow is
not a production release entrypoint. Do not open a GitHub approval issue,
generate an OpenSSL release manifest, or invoke `operator_push.sh` for an
ordinary release. Those scripts remain only for the historical first-container
cutover and explicit recovery work.

The production host keeps default-deny general egress. Its deploy agent may
download only the authorization-bound release artifacts relayed through the
approved Paperclip API routes.

## Release prerequisites

Before creating a candidate:

1. Confirm the target commit is the current `origin/master` SHA.
2. Confirm the required `tests`, `scanner-regression`, and `ci-plugin` checks are
   green for that SHA.
3. Choose the next positive, monotonic release sequence. On-box
   `/var/lib/backbond-deploy-agent/last_activated_sequence` is the authoritative
   last activated value.
4. Confirm the `PAPERCLIP_API_URL` and
   `PAPERCLIP_RELEASE_CANDIDATE_KEY` Actions secrets are configured. Missing
   secrets leave artifacts in GitHub without creating the Paperclip candidate
   and must be treated as an incomplete release.

## Release sequence

1. Dispatch `.github/workflows/scanner-edge-image.yml` from `master` with the
   next release sequence:

   ```sh
   gh workflow run scanner-edge-image.yml --ref master -f sequence=<next>
   ```

2. Wait for the workflow to succeed. It builds the exact `master` SHA, verifies
   the runtime import closure, pushes the immutable image, generates the SBOM
   and offline archive, signs the digest with keyless Cosign, uploads the
   release-candidate artifacts, posts the immutable candidate to Paperclip, and
   requests its approval interaction.
3. In Paperclip, verify the candidate SHA, digest, target host
   (`srv1749248`), environment (`production`), and sequence. The Founder then
   approves that candidate.
4. Create the one-time production authorization for the approved candidate.
   Paperclip stages the archive and signature bundle behind authorization-bound,
   same-origin API paths and issues the approved deploy lease.
5. Install only the authorization id at
   `/run/backbond-deploy-agent/authorization-id` on `srv1749248`, then invoke the
   installed one-shot unit:

   ```sh
   sudo systemctl start backbond-deploy-agent.service
   ```

   Do not place deploy tokens, API keys, or acceptance credentials in argv or
   the unit environment. They are provisioned as encrypted systemd credentials.
6. Confirm the terminal result and public service health:

   ```sh
   systemctl show backbond-deploy-agent.service \
     -p Result -p ExecMainStatus -p ExecMainExitTimestamp
   cat /var/lib/backbond-deploy-agent/current_version
   cat /var/lib/backbond-deploy-agent/last_activated_sequence
   curl -skf https://127.0.0.1/scanner/v1/sla
   ```

   A successful one-shot unit finishes as `inactive (dead)` with `Result=success`
   and `ExecMainStatus=0`. Paperclip must also contain the terminal deploy
   receipt for the same authorization and candidate.

## Trust chain

The release is bound end to end:

`master SHA -> GitHub OIDC-signed image -> immutable Paperclip candidate -> Founder approval -> host/environment/sequence-bound lease -> staged archive and signature hashes -> on-box Cosign and Docker verification -> activated image id -> acceptance receipt`

The deploy agent rejects expired or mismatched leases, replayed sequences,
non-Paperclip artifact URLs, digest or signature mismatches, unsafe Docker
archives, wrong commit labels, and unresolved `activation_pending` evidence.
Cosign verification is pinned to the `scanner-edge-image.yml` workflow identity,
GitHub's OIDC issuer, and `/etc/backbond/sigstore-trusted-root.json`.

## Acceptance and rollback

After activation, the agent runs the production acceptance suite: SLA health,
unauthenticated and forged-key rejection, a dedicated non-`vapi` smoke scan,
scan-log pin verification, and customer-envelope verification.

An acceptance failure restores the previous service image and posts a failed or
rolled-back terminal receipt. A crash after mutation leaves durable
`activation_pending` evidence and blocks later candidates until an operator
reconciles the live service against that record.

The detailed host setup, lease contract, fail-closed checks, credential
provisioning, and receipt semantics are in
[`../../infra/live-edge/backbond-deploy-agent-runbook.md`](../../infra/live-edge/backbond-deploy-agent-runbook.md).

The original systemd-to-container migration remains documented in
[`first-container-cutover.md`](first-container-cutover.md); it is not the normal
release procedure.
