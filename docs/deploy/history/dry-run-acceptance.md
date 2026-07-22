# Deploy-agent staging and rollback acceptance procedure

Run this procedure on an isolated staging host or a production-faithful mirror
using a copy of scanner state. Never use the live `vapi` tenant and never inject
an intentional failure into live production.

## Preconditions

- The candidate commit has passed the required `tests`, `scanner-regression`,
  and `ci-plugin` checks.
- The `scanner-edge-image` workflow can post signed candidates to the staging
  Paperclip environment.
- The mirror has the deploy agent, Docker networking unit, scanner service,
  trusted Sigstore root, encrypted systemd credentials, and root-owned
  deployment configuration installed.
- The acceptance tenant and agent are dedicated staging identities and are not
  `vapi`.
- The candidate uses a new positive sequence and targets the mirror's declared
  host and environment.

## Happy-path proof

1. Dispatch `scanner-edge-image.yml` from the candidate commit with the staging
   sequence.
2. Confirm the workflow succeeds and Paperclip records the same commit SHA,
   immutable image digest, SBOM hash, signature-bundle hash, workflow URL,
   target host, environment, and sequence.
3. Complete the Founder approval interaction in Paperclip.
4. Create a one-time authorization and confirm the approved lease binds the
   candidate, approval interaction, target host, environment, sequence, expiry,
   receipt path, staged archive hash, and staged signature-bundle hash.
5. Install the authorization id through the mirror's systemd credential path
   and start `backbond-deploy-agent.service`.
6. Confirm acceptance reports:
   - `GET /scanner/v1/sla` returns 200.
   - Unauthenticated and forged-key scan requests return 401.
   - A non-`vapi` smoke scan writes a fresh scan-log row.
   - Forensic bundle URI, snapshot hash, and probe-set hash are pinned.
   - The customer envelope contains only the six external fields and no runtime
     fields.
7. Confirm the terminal receipt binds the authorization, candidate, SHA, image
   digest, signature verification, on-box verification, acceptance result,
   rollback status, activated version, and operator/run identity.
8. Confirm `current_version` and `last_activated_sequence` match the receipt.

## Forced-failure rollback proof

On the isolated staging host only, make one acceptance input deterministically
invalid - for example, point the candidate base URL at a local 503 listener or
use an invalid staging-agent identifier. Do not use `vapi` as the failure
mechanism.

1. Deliver another approved candidate through the same Paperclip lease path.
2. Confirm acceptance fails and the candidate does not remain the final serving
   state.
3. Confirm the previous local image is restarted and the SLA health check passes.
4. Confirm the terminal receipt reports the failure reason and successful
   rollback.
5. Confirm a crash or interruption after service mutation leaves
   `activation_pending` evidence and blocks the next sequence until an operator
   reconciles it.

Attach both workflow URLs, the Paperclip candidate and authorization identifiers,
the two terminal receipts, and rollback health evidence to the change record
before authorizing the real production release.
