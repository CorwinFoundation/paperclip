---
artifact_type: other
owner: scanner-engineer-2
persona_audience: internal
phase: 2
canonical_terms_only: true
focus_claim: "BEAAA-17961 freezes seven isolated Python 3.11 environments with exact pins and SHA-256 hashes for the July 31 BackBond Scanner candidate."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-17961", "BEAAA-17956"]
proof_points_cited: ["scripts/verify_python_locks.py", "python311-lock-validation", "n5-calibration-delta.md"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# BEAAA-17961 Python environment freeze

**BLUF.** Runtime, development, regression, Garak, PyRIT, AgentDojo, and
Inspect environments independently install on CPython 3.11/Linux x86_64 with
`pip --require-hashes`; all seven `pip check` results report zero conflicts.

- scanner_version: `0.5.0`
- corpus_version: `2026.6.24`
- probe-set-version: `golden_agents_v1`
- rubric_point_id: `release-wide/all active BackBond Scanner rubric points`
- N=5 calibration delta: `PASS`; per-fixture and per-pillar spread `0.0000`
- contract: `are-scanner-api-v1@v1.0`
- classification: `rule-execution` — dependency/build wiring only
- cto_cosign_ref: `not-required`

## Evidence

- `python311-lock-validation/*-install.log`: independent clean install
  transcripts.
- `python311-lock-validation/*-pip-check.log`: seven zero-conflict results.
- `python311-lock-validation/*-python-version.log`: CPython 3.11 proof.
- `python311-lock-validation/lock-structure.log`: exact-pin, hash, duplicate,
  and framework-leakage verification.
- `python311-lock-validation/lock-sha256s.txt`: committed lock digests.
- `focused-tests.log`: lock-adjacent tests.
- `n5-calibration-delta.md`: golden-set repeatability evidence.

The N=5 helper prints the repository's newer runtime-reported contract stamp
inside its copied legacy report. This artifact remains anchored to the
acceptance contract `are-scanner-api-v1@v1.0`; no contract or output-shape
change was made by BEAAA-17961.
