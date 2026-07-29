---
artifact_type: engineering_evidence
owner: scanner_engineer_2
persona_audience: internal
phase: design_partner_motion
canonical_terms_only: true
focus_claim: "BEAAA-19021 adds a fail-closed exact-allowlist production canary entrypoint for 36 tranche-1 cases at N=5 without changing weighted Scanner state."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-19021", "BEAAA-19020"]
proof_points_cited: ["21 focused tests passed", "180 target calls", "0 judge calls", "N=5 delta 0"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["Promotion requires exact-SHA QA APPROVED, Compliance approval, Underwriting approval, and Founder sign-off."]
---

# BEAAA-19021 Exact-Allowlist Canary Evidence

BLUF: The entrypoint selects only the three approved tool-misuse families, validates the exact 12/12/12 case roster before the first target call, injects the trusted tool-capability declaration, and caps execution at 180 target calls with no judge.

- Classification: rule execution under the existing-operation smoke-test and behavior-preserving refactor rows in `scanner-implementation-supervision-v1@v1.1`.
- Contract: `are-scanner-api-v1@v1.0`.
- Scanner version: `0.5.0`.
- Corpus version: `fa32e0e081ffbd2953ea3df8aa8d2d0faa0464a3076d17dad9b1758804488988`.
- Probe-set version: tranche-1 mappings pinned in the emitted manifest.
- `rubric_point_id`: `ROBUSTNESS.TOOL_MISUSE.*`; no rubric edit.
- N=5 calibration delta: absolute Δ=0 for legacy total, every pillar, band, eligibility, pricing, premium, quote/policy behavior, and published weighted/countable totals.
- Upstream eval version: unchanged; deterministic structured invariant, zero LLM judge calls.
- `cto_cosign_ref`: not required for rule execution.

Verification:

```text
pytest -q tests/test_beaaa19021_tranche1_canary.py tests/test_beaaa18953_tranche1_observational.py
21 passed in 23.48s
```

Operator command:

```sh
exec 9</run/secrets/tranche1-target-token
BACKBOND_RUNTIME_COMMIT_SHA=<candidate-sha> \
BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA=<candidate-sha> \
python -m tools.tranche1_canary_runner \
  --agent-endpoint <production-local-endpoint> \
  --tenant-id <tenant-id> \
  --authorization-ref <opaque-root-custody-reference> \
  --job-id <canary-job-id> \
  --commit-sha <candidate-sha> \
  --image-ref <immutable-image-reference> \
  --evidence-dir /var/lib/are-scanner/evidence \
  --secret-fd 9
```

Rollback command:

```sh
BACKBOND_TRANCHE1_OBSERVATIONAL=off <restore-retained-pre-candidate-image-and-config-command>
```

No production execution or promotion was performed. The exact candidate must pass the canonical QA child with evidence-backed `APPROVED` for the same SHA, then Compliance, Underwriting, and Founder production gates.
