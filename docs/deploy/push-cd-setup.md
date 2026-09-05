---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "Master pushes deploy the BackBond Scanner development target automatically after the image build."
contracts_referenced: ["are-scanner-api-v1"]
ticket_refs: ["BEAAA-29900"]
proof_points_cited: ["same-run immutable image artifact", "pinned SSH host key", "on-box helper rollback"]
version: 0.7
prior_version: "docs/deploy/push-cd-setup.md@0.6"
self_audit_passed: true
escalations_noted: []
---

# Scanner push deployment

Every push to `master` builds and signs an immutable scanner image in
`.github/workflows/scanner-edge-image.yml`. After the build job succeeds, the
workflow's `deploy` job downloads that exact run's archive, copies it to
`are-deploy@srv1749248`, and invokes `/usr/local/sbin/scanner-deploy`.

The repository needs two pinned SSH secrets:

- `SCANNER_DEPLOY_SSH_KEY`: the private key for the restricted `are-deploy` user.
- `SCANNER_DEPLOY_KNOWN_HOSTS`: the pinned `known_hosts` line for
  `srv1749248.hstgr.cloud`. The workflow does not use trust-on-first-use.

`PAPERCLIP_API_URL` and `PAPERCLIP_DEPLOY_AGENT_TOKEN` let the final step post
the deployment receipt to BEAAA-29900. Receipt posting runs even when activation
fails, and does not replace the workflow result.

The server never builds or pulls an image. The existing root-owned helper checks
the archive hash, OCI revision, and embedded release identity, then updates the
systemd service atomically. It checks readiness and restores the prior image on
failure. The workflow verifies the public release identity and SLA endpoint after
activation. A broken master is fixed forward.

The manual `workflow_dispatch` evidence lane remains available and does not run
the deploy job.
