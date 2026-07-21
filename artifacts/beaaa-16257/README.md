---
artifact_type: provenance_manifest
owner: cto
persona_audience: founder
phase: 1
canonical_terms_only: true
focus_claim: "This pack provides the signed, repository-qualified Sequence 20 candidate identity requested on BEAAA-16257."
contracts_referenced: ["are-scanner-api-v1"]
ticket_refs: ["BEAAA-16257", "BEAAA-16232", "BEAAA-15400", "BEAAA-15373"]
proof_points_cited:
  - "Candidate source is exactly 46eec2f1887800c2359a1cf75a06657e4eb4d844."
  - "Candidate OCI digest is sha256:fe795b8edd43f91f19350825bf3fbe81f51403ad756bc2eaf7d0cbc1fa1184c9."
  - "The image was keyless-signed and attested in GitHub Actions run 29819869273."
version: 1.0
prior_version: null
self_audit_passed: true
escalations_noted:
  - "Fresh QA APPROVED and Founder promotion sign-off are still required."
---

# BEAAA-16257 Sequence 20 release-candidate evidence

`sequence-20-release-manifest.json` binds candidate source commit `46eec2f…d844` to the repository-qualified OCI image, the checksummed inbound image/source archives, the SBOM, and the GitHub OIDC image signature and provenance attestation. Its detached signature verifies with the committed public key:

```sh
openssl dgst -sha256 \
  -verify artifacts/beaaa-16257/sequence-20-release-manifest.pub.pem \
  -signature artifacts/beaaa-16257/sequence-20-release-manifest.json.sig \
  artifacts/beaaa-16257/sequence-20-release-manifest.json
```

This candidate evidence does not authorize provisioning or promotion. The image/source archives remain in the immutable GitHub Actions release artifact and are transferred only through the approved operator-mediated inbound channel.
