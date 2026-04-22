# BMI Scanner v0.1

BEAI Maturity Index Scanner. 100-point governance and pricing engine for production AI systems.

## Phase 1 Scope (through end of May 2026)

- 4 pillars × 24 dimensions deterministic rubric (locked through 2026-04-28)
- Draft pillar weights: P1=35, P2=20, P3=30, P4=15
- BEAAA-21 v0.2 API contract to the Underwriter (`/score`, `/forensic_bundle`)
- HTML-only Report Card render (BEAAA-13 rev 4 template)
- Hosted-eval-only intake (no on-prem connector this phase)
- Sequential pillar scoring (parallel deferred)

## Operating Mode

Mode A endorsed by CEO 2026-04-22 (BEAAA-29):
- Solo CTO + Claude Code execution
- Zero Phase 1 human hires
- Mode A→B trigger gate live (any §9.6 risk yellow → fire Staff ML Eval hire within 5 business days)

## Layout

```
rubric/         # Pillar/dim definitions, weights, scoring functions (WS1)
api/            # BEAAA-21 v0.2 OpenAPI surface (WS2)
policy/         # BEAAA-22 data-handling enforcement (WS3)
report_card/    # HTML render templates + generator (WS4)
tests/          # Synthetic fixtures, contract tests
```

## Red Lines

Snapshot integrity, `forensic_bundle_uri`, tenant isolation, audit log, pillar weights, dim count. Non-negotiable in any mode.
