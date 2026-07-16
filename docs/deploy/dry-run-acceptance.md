# CD dry-run acceptance procedure

Run this procedure on an isolated staging host or a production-faithful mirror
using a copy of scanner state. Never use the live `vapi` tenant and never inject
an intentional failure into live production.

## Preconditions

- The branch containing the deployment changes has passed Linux CI, including
  Bash syntax and archive-verification tests.
- Repository Issues are enabled and the approval issue is assigned to
  `CorwinFoundation`.
- `CD_MANIFEST_SIGNING_KEY_PEM` is configured in GitHub; its matching public key
  is preinstalled at `/etc/are-scanner/cd-manifest-signing-pubkey.pem`.
- `box_deploy`, `operator_push.sh`, acceptance checks, deploy-record helper, and
  root-owned deployment configuration are provisioned on the staging host.
- `STAGING_TENANT_ID` identifies a non-`vapi` tenant and the state directory is a
  staging copy.

## Happy-path proof

1. Trigger the workflow for the current `master` commit.
2. Confirm the image build produces `image-payload-<sha>` and that the approval
   issue names the same SHA, registry digest, and archive SHA-256.
3. Approve the issue from `CorwinFoundation`.
4. Confirm `release-manifest-<sha>` is created and its signed fields match the
   image artifact and Actions run.
5. Transfer the archive, manifest, and signature to the staging host. Run the
   operator command using the preinstalled public-key trust anchor.
6. Confirm acceptance reports:
   - `GET /scanner/v1/sla` returns 200.
   - Unauthenticated and forged-key scan requests return 401.
   - A non-`vapi` smoke scan writes a fresh scan-log row.
   - Forensic bundle URI, snapshot hash, and probe-set hash are pinned.
   - The customer envelope contains only the six external fields and no runtime
     fields.
7. Confirm the deploy record contains SHA, digest, checks, approver, timestamp,
   and the Actions run URL.

## Forced-failure rollback proof

On the isolated staging host only, make one acceptance input deterministically
invalid—for example, point the candidate base URL at a local 503 listener or use
an invalid staging-agent identifier. Do not use `vapi` as the failure mechanism.

1. Deliver another approved candidate through the same signed-artifact path.
2. Confirm acceptance fails and activation does not become the final serving
   state.
3. For a first-cutover mirror, confirm Nginx still serves or is restored to the
   legacy port and the legacy service remains active.
4. For a container-to-container update, confirm the previous local image is
   restarted.
5. Confirm rollback health passes and the deploy record reports failure and
   rollback evidence.

Attach both Actions run URLs, the two deploy records, and the rollback health
evidence to the change record before authorizing the real production cutover.
