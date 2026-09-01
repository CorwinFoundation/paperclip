---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This runbook keeps BackBond Scanner production promotion behind exact-candidate evidence and Founder approval."
contracts_referenced: ["are-scanner-api-v1"]
ticket_refs: ["BEAAA-25460", "BEAAA-25461", "BEAAA-28248"]
proof_points_cited: ["GitHub protected review", "Sigstore keyless signing", "CycloneDX SBOM", "in-toto provenance"]
version: 0.3
prior_version: "docs/deploy/push-cd-setup.md@e79d33dc"
self_audit_passed: true
escalations_noted: []
---

# Governed Scanner release evidence

Status: the former direct-production workflow remains quarantined under
BEAAA-25460 and BEAAA-25461. BEAAA-28248 adds a manual, evidence-only release
lane. It does not deploy, copy files to, activate, or roll back production.

The quarantined controls remain in force:

- `.github/workflows/deploy.yml` must not exist;
- merging to `master` must not build or publish a deployable image;
- repository workflows must not hold or use production SSH credentials;
- the self-hosted direct-deployment route from Git history must not return.

## Evidence-only release lane

An authorized operator may manually dispatch
`.github/workflows/scanner-edge-image.yml` in one of two modes.

1. With blank release inputs, it builds and publishes an immutable image for an
   exact commit on `master`, verifies its embedded release identity and runtime
   closure, and uploads the image archive.
2. With `source_sha`, `expected_image_digest`, `source_issue_id`, and a positive
   `sequence`, it pulls that exact digest, verifies the OCI revision and embedded
   release identity, generates a CycloneDX SBOM, creates and verifies a Sigstore
   bundle, attests both the SBOM and SLSA provenance, validates the release
   manifest, and uploads the evidence bundle for 90 days.

Each Cosign sign, verify, attest, and attestation-verification operation is a
separate visible gate with a 90-second command timeout. A stalled trust-service
or registry call therefore fails at the exact phase instead of consuming the
workflow's cumulative timeout without actionable evidence.

The workflow runs only through `workflow_dispatch`, requires the workflow ref to
be `master`, and requires a release-evidence source commit to equal current
`master`. It contains no SSH, SCP, production secret, Paperclip mutation, or
host-activation step.

## Production gate

The evidence bundle is not deploy authority. Promotion still requires all of
the following:

1. a separately governed proposal and protected GitHub review;
2. independent QA for the exact source commit and release evidence;
3. a Paperclip release candidate whose digest, SBOM hash, signature bundle,
   provenance, workflow run, environment, target host, and sequence match;
4. a fresh Founder approval on that exact candidate;
5. activation through the current on-box fail-closed path, with health checks,
   a terminal receipt, and automatic rollback on failure.

Any mismatch stops the release. Historical direct-deploy instructions remain
read-only evidence and are not authorization to restore the retired path.
