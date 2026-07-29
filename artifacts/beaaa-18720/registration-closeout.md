# BEAAA-18720 local runtime registration closeout

The accepted 2,000-row Paperclip content inventory is registered on the
isolated local branch `feat/register-2000-probes`, based on GitHub master
`f43f37f198df5191388ff7ab7802c0cacfc3aaae`.

## Result

- Accepted inventory: 2,000 rows across 87 families.
- Accepted inventory SHA-256:
  `fa32e0e081ffbd2953ea3df8aa8d2d0faa0464a3076d17dad9b1758804488988`.
- Registered inventory cases: 2,000.
- Cases using the new pinned candidate adapter: 1,782.
- Cases reusing an existing executable family/case adapter: 218.
- Total registry rows: 2,242.
- Active executable registry rows: 2,202.
- Target-bound runtime rows before identity collapse: 2,213.
- Distinct runtime tests: 2,201.

The 2,000 candidate cases remain `reported_not_scored`. Their registration
does not change measured score math or invent rubric mappings. Applicability,
oracle, evidence-field, provenance, semantic-fingerprint, and dedupe metadata
remain bound to the accepted artifact.

## Runtime boundary

The `content_candidate` worker is pinned to the accepted artifact hash and is
marked `measured_score_allowed=false`. It materializes independent pairs,
structured scenarios, and multi-turn conversations, invokes the submitted
agent, applies the pinned oracle contract through a deterministic-temperature
judge, and writes standard per-case evidence.

The default local full-scan limits are 6,000 target calls, 4,000 judge calls,
$100 maximum estimated judge cost, and six hours. The 1,782 cases using the
new adapter plan 2,805 target calls and 2,468 judge calls; existing adapters
fit within the remaining call budget.

## Verification

Run:

```text
python -m tools.validate_content_candidate_registration
```

This command is read-only and fails closed on artifact hash/count drift,
registry duplication, worker binding, published count, and distinct-runtime
count.

GitHub publication, CI integration, deployment, and production promotion are
not part of this local registration change.
