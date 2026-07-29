---
artifact_type: engineering_evidence
owner: scanner_engineer_2
persona_audience: internal
phase: design_partner_motion
canonical_terms_only: true
focus_claim: "BEAAA-19021 adds a fail-closed exact-allowlist production canary entrypoint for 36 tranche-1 cases at N=5 without changing weighted Scanner state."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-19021", "BEAAA-19023", "BEAAA-19020", "BEAAA-19052", "BEAAA-19093"]
proof_points_cited: ["63 focused tests passed", "invalid authority and identity inputs rejected at 0 target calls and 0 evidence writes", "180 target calls", "0 judge calls", "N=5 delta 0"]
version: 0.6
prior_version: 0.5
self_audit_passed: true
escalations_noted: ["Promotion requires exact-SHA QA APPROVED, Compliance approval, Underwriting approval, and Founder sign-off."]
---

# BEAAA-19021 Exact-Allowlist Canary Evidence

BLUF: The entrypoint selects only the three approved tool-misuse families, validates the exact 12/12/12 case roster before the first target call, injects the trusted tool-capability declaration, and caps execution at 180 target calls with no judge.

BEAAA-19023 remediation binds the emitted manifest to the actual runtime and
authorized commit and requires a repository-qualified immutable image digest.
Malformed or mismatched commit identities and mutable or malformed image
identities fail before executor construction and therefore at zero target calls.
The converged BEAAA-19046 remediation requires an independently issued
Ed25519-signed promotion lease and Founder authorization bound to production
environment, tenant, exact issue pair, candidate and runtime identities,
allowlist hash, call ceilings, approval identity, and validity window. The
verification-only runner atomically consumes the exact lease scope in a shared
writable PostgreSQL authority before executor construction. The authority
validates its storage identity, continuity epoch, schema, and writable-primary
health; authority, replay, continuity, and storage failures assert zero calls
and zero evidence writes.

- Classification: rule execution under the existing-operation smoke-test and behavior-preserving refactor rows in `scanner-implementation-supervision-v1@v1.1`.
- Contract: `are-scanner-api-v1@v1.0`.
- Runtime manifest: `api_contract` is pinned exactly to `are-scanner-api-v1@v1.0`; a top-level key-set assertion prevents response-shape drift.
- Canary-runner SHA-256: `4c3c15d9688c5ebc8fdb052e06194c5d6fb3efdf7923779bbb318200ea3ff959`.
- Deterministic canonical manifest content SHA-256: `819dfc88451735899a88e025f43530a41900572473373ce0c86b07c47fb38bb2`. The pin excludes measured `budget.elapsed_seconds`.
- Scanner version: `0.5.0`.
- Corpus version: `fa32e0e081ffbd2953ea3df8aa8d2d0faa0464a3076d17dad9b1758804488988`.
- Probe-set version: tranche-1 mappings pinned in the emitted manifest.
- `rubric_point_id`: `ROBUSTNESS.TOOL_MISUSE.*`; no rubric edit.
- N=5 calibration delta: absolute Δ=0 for legacy total, every pillar, band, eligibility, pricing, premium, quote/policy behavior, and published weighted/countable totals.
- Upstream eval version: unchanged; deterministic structured invariant, zero LLM judge calls.
- `cto_cosign_ref`: not required for rule execution.

Verification:

```text
python3 -m pytest -q tests/test_beaaa19021_tranche1_canary.py tests/test_beaaa19052_shared_lease_authority.py tests/test_beaaa18953_tranche1_observational.py
63 passed
```

Operator command:

```sh
exec 9</run/secrets/tranche1-target-token
BACKBOND_RUNTIME_COMMIT_SHA=<candidate-sha> \
BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA=<candidate-sha> \
BACKBOND_RUNTIME_IMAGE_REF=<repository@sha256:digest> \
BACKBOND_RUNTIME_ENVIRONMENT=production \
BACKBOND_RUNTIME_TENANT_ID=<tenant-id> \
BACKBOND_PROMOTION_LEASE_AUTHORITY_DSN=<production-authority-dsn> \
BACKBOND_PROMOTION_LEASE_STORAGE_IDENTITY=<production-authority-storage-identity> \
BACKBOND_PROMOTION_LEASE_CONTINUITY_EPOCH=<production-authority-continuity-epoch> \
python -m tools.tranche1_canary_runner \
  --agent-endpoint <production-local-endpoint> \
  --tenant-id <tenant-id> \
  --authorization-ref <opaque-root-custody-reference> \
  --job-id <canary-job-id> \
  --commit-sha <candidate-sha> \
  --image-ref <repository@sha256:digest> \
  --promotion-lease <signed-promotion-lease.json> \
  --founder-authorization <signed-founder-authorization.json> \
  --evidence-dir /var/lib/are-scanner/evidence \
  --secret-fd 9
```

Rollback command:

```sh
BACKBOND_TRANCHE1_OBSERVATIONAL=off <restore-retained-pre-candidate-image-and-config-command>
```

No production execution or promotion was performed. The exact candidate must pass the canonical QA child with evidence-backed `APPROVED` for the same SHA, then Compliance, Underwriting, and Founder production gates.
