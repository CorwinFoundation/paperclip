# ADR-0062: Behavioral-integrity scoring activation candidate

BLUF: BEAAA-22856 activates verified deterministic behavioral evidence as an
additional required pass condition on six existing rubric points without
changing weights, bands, dimensions, or the API output shape.

- Classification: rule change under `scanner-implementation-supervision-v1@v1.0`.
- Contract: `are-scanner-api-v1@v1.3.0`.
- Authority: CTO specification on BEAAA-22856; design and frozen evidence from BEAAA-22775.
- Pins: scanner 0.5.0, corpus 0.1.0, seeds 0..4.

The adapter verifies the content digest and complete fixture/trace/grader chain
for each observation. A failure sets only its named existing point to raw zero.
A pass retains the prior raw, so it cannot override other failing evidence.
Error and not-applicable observations retain existing scoring and are recorded
in the forensic audit rows. Separate predicates mapped from one family cannot
penalize each other.

Free-text claim extraction remains inactive. Only deterministic evidence already
rendered by the approved harness may enter this adapter.

Rollback: revert the BEAAA-22856 activation commit. BEAAA-22775 render-only
design and calibration evidence remain intact.
