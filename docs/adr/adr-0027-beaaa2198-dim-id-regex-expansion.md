# ADR-0027 — BEAAA-2198: CLI `_DIM_ID_RE` expansion to accept `Pillar.FAMILY.SUB` dim_ids

- Status: Accepted
- Date: 2026-06-05
- Ticket: [BEAAA-2198](/BEAAA/issues/BEAAA-2198)
- Classification: Rule-change per `scanner-implementation-supervision-v1@v1.0`, row 32 (CLI behaviour change)
- CTO co-sign (pre-code): comment `f8adf83e` on BEAAA-2198
- Supersedes / superseded by: none
- Master base: `0780b18`

## Context

Rubric v1.0.1 introduces a second, fully-qualified `dim_id` naming shape — `Pillar.FAMILY.SUB` (strict 3-segment, all-caps; e.g. `ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL`) — alongside the existing legacy `P<n>.<m>` shape (e.g. `P1.1`, `P3.4`). The four Pillar-shaped `id:` values currently in `rubric/rubric.yaml` v1.0.1 are:

- `GOVERNANCE.PII.HANDLING_ATTESTATION`
- `ROBUSTNESS.PII.SCAN_TIME_DETECTION`
- `ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER`
- `ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL`

On master `0780b18`, `harness/cli_runner.py` validates dim_ids on the CLI input-acceptance surface via inline `re.match(r"^P\d+\.\d+$", …)` at two call sites:

- L173 — `_validate_eval_results` (eval_results.json keys)
- L262 — `load_inputs` applicability_decisions block (applicability.json `applicability_decisions` keys)

P-numeric-only acceptance is the gate that prevents the rubric v1.0.1 naming surface from being usable end-to-end on the CLI delivery layer. This ADR records the expansion to a union regex that accepts both shapes.

## Decision

Introduce a module-level `_DIM_ID_RE` constant in `harness/cli_runner.py`:

```python
_DIM_ID_RE = re.compile(
    r"^(?:P\d+\.\d+|[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*)$"
)
```

Replace the two inline `re.match(...)` call sites with `_DIM_ID_RE.match(...)`. Update the two corresponding error messages from `"not a P<n>.<m> dim id"` to `"not a P<n>.<m> or Pillar.FAMILY.SUB dim id"`.

### Why strict 3-segment, not generic `(?:\.[A-Z0-9_]+)+`

The CLI input surface is adversarial-input code (`harness/cli_runner.py:13-15`). The tighter, strict-3-segment shape is the more defensible choice:

- The four Pillar-shaped `id:` values in rubric v1.0.1 are all exactly 3 segments. There is no current need for 4+.
- The generic `(?:\.[A-Z0-9_]+)+` alternative would accept 4+ segments and digit-led sub-segments (e.g. `X.Y.Z.W`, `X.1FOO.BAR`) that no rubric currently emits.
- A future v1.0.2+ rubric introducing a 2-segment family-level or a 4-segment hierarchy dim_id is a follow-up regex amendment with its own CTO co-sign — not an accept-everything-now pattern.

### Why no `are-scanner-api-v1` contract version bump

This change touches only the CLI input-acceptance surface. Output JSON shape is unchanged. `contracts/are-scanner-api-v1.md@v1.2.0` does not normatively define the `dim_id` string shape (confirmed via grep on master `0780b18` — the only mention is BEAAA-2139 §change-log boilerplate). The contract row 32 "contract version bump" parenthetical fires on output JSON shape changes, which does not apply here.

## Consequences

- **Superset guarantee for legacy goldens.** The P-numeric branch of the union is byte-equivalent to the original regex, so all existing fixtures and customer inputs that use `P<n>.<m>` continue to validate identically. The N=5 calibration packet on B1 / G1 / G2 / M1 / M2 / M3 (spread=0 on `are_total` and every pillar + full-dict byte-equivalence vs. master) is the binding superset proof.
- **Downstream scoring unaffected at this layer.** `_validate_eval_results` only validates dim_id shape and entry shape; rubric-existence and scoring math live in `rubric.scoring.score` and are unchanged by this ADR.
- **Adversarial-input safety preserved.** The strict 3-segment Pillar shape is non-overlapping with `P<n>.<m>` (P-prefix-digit vs. all-caps-first-letter) and disallows lower-case, digit-led sub-segments, and 4+ segments — so the accepted-input set is precisely "legacy P-numeric ∪ exact-3-segment all-caps Pillar".
- **Test coverage.** `tests/test_cli_runner_beaaa464.py::InputLoaderTests::test_eval_results_accepts_pillar_family_sub_dim_id` pins the new acceptance contract; the existing `test_eval_results_rejects_bad_dim_id` continues to reject malformed inputs.

## Alternatives considered

- **Generic `(?:\.[A-Z0-9_]+)+` tail** — rejected as too permissive for adversarial input; reserves no co-sign gate for future shape extensions.
- **Two separate constants** — `_LEGACY_DIM_ID_RE` and `_PILLAR_DIM_ID_RE` with two separate match calls. Rejected: doubles the call-site complexity and obscures the single-acceptance-set intent.
- **Reject `P<n>.<m>` and force migration to Pillar shape** — out of scope; breaks every legacy customer input and every existing golden. The two shapes must coexist for the rubric v1.0.0→v1.0.1 transition window and beyond.

## References

- Design comment (pre-code): [BEAAA-2198 comment 180dc5aa](/BEAAA/issues/BEAAA-2198#comment-180dc5aa-0479-4ccb-a859-ac67c31bab57)
- CTO pre-code co-sign: [BEAAA-2198 comment f8adf83e](/BEAAA/issues/BEAAA-2198#comment-f8adf83e-567b-4007-b9da-f21b6324e7b0)
- Workspace-hygiene precedent: [BEAAA-2175 comment 954754f5](/BEAAA/issues/BEAAA-2175#comment-954754f5-4ef7-4cba-ad00-3465d4beb7e1)
- Supervision contract: `scanner-implementation-supervision-v1@v1.0`
