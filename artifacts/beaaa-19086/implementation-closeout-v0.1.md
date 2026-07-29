---
artifact_type: implementation_closeout
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "The remediated Dynamic Score vNext candidate produces replayable, fail-closed shadow evidence while leaving the public BackBond Score and every underwriting decision byte-identical."
contracts_referenced:
  - "are-scanner-api-v1@v1.11.0 (current additive patch lineage; no output-shape change)"
  - "scanner-implementation-supervision-v1@v1.1"
ticket_refs: ["BEAAA-19086", "BEAAA-19054", "BEAAA-19079", "BEAAA-19080", "BEAAA-19087", "BEAAA-19088", "BEAAA-19089"]
proof_points_cited:
  - "candidate commit 593f8b8f236d3e85b814f14078f596985397bcfe"
  - "tests/test_dynamic_score_vnext.py: 11 passed"
  - "golden fixtures B1, G1, G2, M1, M2, M3: N=5 public score and band delta 0"
version: "v0.1"
prior_version: "artifacts/beaaa-19076/implementation-closeout-v0.1.md"
self_audit_passed: true
escalations_noted:
  - "Rule-change candidate under scanner-implementation-supervision-v1@v1.1 row Change scoring math; activation requires CTO and Underwriter co-sign. This issue produces a default-off shadow candidate only."
scanner_version: "0.5.0"
corpus_version: "tests/fixtures/golden_agents expected-score fixtures at candidate commit"
probe-set-version: "dynamic-score-vnext@v0.1-remediation.1"
rubric_point_id: "shadow-only research path; no public rubric contribution"
n5_calibration_delta: "0 on B1, G1, G2, M1, M2, M3; five public-output reads each"
cto_cosign_ref: "pending; required before any activation, not required to create this immutable shadow candidate"
merged_sha_on_canon_master_at_close: "not applicable while candidate is in review and not merged"
---

# BEAAA-19086 implementation closeout

## BLUF

This is a rule-change shadow implementation for the CTO and reviewers that
remediates replay and fail-closed defects without changing any public BackBond
Score or underwriting output. The immutable code candidate is
`593f8b8f236d3e85b814f14078f596985397bcfe`; it remains default-off and is not
authorized for activation or promotion.

## Remediation

- The receipt preimage now binds `as_of`, raw evidence units, evidence IDs,
  timestamps, reason codes, evidence references, current-score provenance and
  version, the expected-family manifest, parameter SHA, model SHA, and
  calculator code SHA.
- Missing expected families emit explicit `invalid_missing` rows. Invalid,
  conflicting, and unproven-not-applicable evidence fail closed with reason
  codes and evidence references and remain visible to the denominator.
- Recency decay is applied to each evidence unit before family aggregation. One
  fresh unit cannot refresh stale peers.
- `adverse_severity` is pinned to `[0,4]`, where larger is worse. Increasing
  adverse severity cannot increase the dynamic component.
- Non-severe candidate movement is clamped to
  `abs(shadow_score - current_score) <= 1.00`. High-severity evidence emits a
  separate `high_severity_referral` signal and does not bypass the score cap.
- The explicit `enabled=False` boundary remains default-off. The sink remains
  an isolated, content-addressed receipt directory, and a test proves the
  serialized public decision object is byte-identical before and after shadow
  execution.

## Exact pins

- Candidate commit: `593f8b8f236d3e85b814f14078f596985397bcfe`
- Parent implementation commit: `e5645f9f8b36bb0ad0c567a15af02f5cfb7ba107`
- Calculator SHA-256:
  `31ac8b02b62ccb85a4c89ed044bd175078d2aa278856777ac106a7a00af54e24`
- Parameter SHA-256:
  `7c044525a86324f7d9080e7a8aad878eecf13b9c3ff62dba245014a9df0fc10c`
- Model SHA-256:
  `80504454ab6076e10b2b0a035c19ff7da72bd043cf53b7ce1eaad1e912c70101`
- Contract lineage: `are-scanner-api-v1.md` base through the merged
  `are-scanner-api-v1-v1.11.0-patch.md` at `f171218`. This candidate adds no
  public API field and does not bump the contract.

## Verification

- `python3 -m pytest -q tests/test_dynamic_score_vnext.py`: 11 passed.
- Receipt mutation coverage: evidence ID and `as_of` changes alter the receipt.
- Expected-family coverage: a wholly absent family emits
  `EXPECTED_FAMILY_ABSENT` and sets `fail_closed=true`.
- Anti-refresh and adverse-severity monotonicity: passed.
- Absolute movement cap: passed.
- Public decision bytes before and after enabled shadow execution: identical.
- Golden N=5: B1 37, G1 80, G2 78, M1 58, M2 47, M3 55 on all five reads.
  Public score, band, eligibility, pricing, premium, quote, policy, and
  underwriting delta: 0.

## Rollback and review state

Rollback owner: Scanner Engineer. Action: leave the invocation disabled, stop
the shadow caller if one is introduced, and delete/revert candidate commit
`593f8b8f` from its review branch. Existing public and underwriting behavior
requires no rollback because this candidate has no decision-output write path.

Exact-SHA review is routed through BEAAA-19087 (QA), BEAAA-19088
(Underwriting), and BEAAA-19089 (Compliance). Any new commit supersedes this
candidate and invalidates every review.

## FAL audit

- Focus: pass. The candidate strengthens reproducible BackBond Scanner evidence.
- Alignment: pass. It makes no coverage, pricing, activation, or customer claim.
- Linkage: pass. The public BackBond Score remains the sole decision output.
