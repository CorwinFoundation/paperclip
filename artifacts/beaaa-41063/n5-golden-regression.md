# BEAAA-41063 N=5 golden regression

Command: `python3 scripts/beaaa6190_v05_rc_n5.py`

- Seeds / repetitions: `0..4` / `N=5` deterministic replay.
- Live roster: B1, G1, G2, M1, M2, M3. Pending baseline slots remain B2, B3, G3.
- Result: **PASS**; all five runs for every live fixture were byte-identical to the locked expected score.
- Maximum total delta: **0**.
- Maximum pillar delta: **0.0000**.
- Total and per-pillar spread: **0** for every live fixture.
- Paid hosted spend: **0**. Local verification only; required remote CI remains queue-only on `backbond-ci`.

This activation is founder-development-only and zero-weight. It does not change rubric weights, scoring math, bands, underwriting state, or priced output.
