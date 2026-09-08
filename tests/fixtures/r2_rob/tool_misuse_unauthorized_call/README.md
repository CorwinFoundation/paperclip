# Unauthorized-call Day One offline fixtures

BEAAA-30571 adds the 20 `UC-EXT-001` through `UC-EXT-020` cases in
`day_one_v1.yaml` to the 12 existing `TOOL_MISUSE_UC-01` through
`TOOL_MISUSE_UC-12` probes. The family catalogue therefore contains 32 unique
case IDs; aliases, paraphrases and replicates are not counted.

The cases send synthetic authorization state to
`harness/mock_tools/authorization_policy.py`. An allowed case returns a
sanitized `permitted` receipt and records only the named in-memory
`fixture_draft` effect. A denied case returns a sanitized
`authorization_required` receipt with no protected value and no effect.

BEAAA-30584 replaces nine of the ten odd-numbered cases. As first landed they
re-asserted a default value, so the 20 IDs resolved to 11 distinct states.
UC-EXT-001 stays as the clean all-permitted baseline; UC-EXT-003 through 019
now each vary a different authorization dimension or a different mode of one
(absent, explicitly negative, wrong type, near-miss value, under the limit).
The pack is now 20 IDs and 20 distinct states, checked by
`test_the_20_cases_resolve_to_20_distinct_states`.

Run `pytest -q tests/test_beaaa30571_unauthorized_call_day_one.py`. The tests
also prove the independent oracle catches a deliberately incorrect allow and
that empty or malformed receipts fail harness validation. All numbers and
thresholds are illustrative fixture assumptions, not product defaults.

Limitations: this offline binding uses deterministic mock state. It does not
activate production dispatch, scoring, underwriting, network access or a live
target probe. The existing Day One production verifier binding remains absent.
