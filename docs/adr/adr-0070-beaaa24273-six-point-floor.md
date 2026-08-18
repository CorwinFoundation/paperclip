# ADR-0070: Additive six-point 30-case floor

- Ticket: BEAAA-24273
- Status: accepted
- Classification: rule-execution, existing deterministic probe implementation
- Contract: `contracts/are-scanner-api-v1.md@v1.3.0`
- Supervision: `scanner-implementation-supervision-v1@v1.1`

## Decision

Add six new, versioned, deterministic single-turn families with ten concrete behavior scenarios each. Each scenario names a rubric-specific situation and pairs a safe expected response with a materially unsafe negative fixture. Keep the BEAAA-24210 family corpora immutable. The new families reuse the active registry and `scored-lane-substring-oracle@1.0.0`; they do not change rubric weights, scoring math, judge models, API shape, contract output, tamper resistance, or published counts.

Each case has a distinct prompt hash and oracle hash, an explicit binary PASS/FAIL disposition, a negative fixture, pinned authored provenance, and a reachable `yaml://` evidence reference. Strict registry validation must identify every row as canonical, scored, execution-evidenced, target-bound, and free of runtime aliases.

## Consequences

The six target points move from 20 to 30 canonical scored cases, adding 60 cases in total. Rollback is `git revert <BEAAA-24273-candidate-sha>`.

The original candidate `9736dbf70f0cb3b7504d8f8b9268e1c0b031f768` is superseded because its numbered placeholder prompts and tautological markers did not demonstrate rubric-specific behavior.
