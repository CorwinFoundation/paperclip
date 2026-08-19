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

The integration candidate is based on exact canonical upstream `71c5124d994ac007837712f51c4b21dbb17d9aa8`, preserving the overlapping BEAAA-24310 registry and canary reconciliation. The functionally approved pre-integration candidate `b848816f1adaa8447974816a7e489b531ce2b6d7` is superseded and must not be promoted. N=5 evidence compares seeds 0 through 4 between `71c5124d...` and the integration tree; rollback remains a revert of the final immutable integration candidate.

## Integration finding: upstream exact-count guards

Upstream `71c5124d...` (BEAAA-24310) repaired three exact-count guards that BEAAA-24210 had left stale at 2,244. Those guards now pin the active-executable count at 2,279, so adding 60 executable rows turns them red:

- `tests/test_active_test_registry_beaaa13644.py::test_production_registry_counts_canonical_runtime_identities_once` — pins 2,279 active executable, 2,290 target-bound, 2,031 promoted, and asserts active executable equals the published count.
- `tests/test_authoritative_probe_registry_beaaa20357.py::test_executable_claim_is_not_default_executed_reported_or_scored` — pins generated `category_counts.executable` at 2,279.
- `tests/test_canonical_test_registry_beaaa13762.py::test_beaaa13992_active_rows_count_only_target_bound_execution_evidence` — pins 2,279 in both registry metadata and row count.

This candidate does not touch those pins and does not regenerate `tools/probe-registry/authoritative_registry.json`, because BEAAA-24272 forbids published-count changes and `load_published_count` derives the published count from that generated file. `tools/active_test_registry.py --check` stays green: its validator fails only when the published count exceeds the active executable count, so publishing 2,279 while 2,339 are executable is a deliberate under-claim.

The guards therefore encode a stricter invariant (published equals active executable) than the validator does. Reconciling them requires either republishing at 2,339 or relaxing the equality, and both are claim-surface decisions outside this ticket. Left for CTO.

Separately, `scripts/authoritative_probe_registry_integration_gate.sh` still names `test_legacy_2244_is_not_default_executed_reported_or_scored`, which upstream renamed. The gate script is stale on `71c5124d...` independently of this change and is left untouched here.

Canonical master `71c5124d...` also already fails `python3 -m tools.probe_registry --repo . --check` on `registered` (2,608 committed against 2,653 generated) and `fixture_test_only` (89 against 99). That drift predates this candidate.
