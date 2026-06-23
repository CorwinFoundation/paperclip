# BEAAA-7902 EdDSA Signed-Wire N=5 Calibration Delta

Generated: 2026-06-23T20:34:39+00:00

BLUF: PASS. The collector/receiver signed-wire implementation does not touch scoring; locked golden-set N=5 reproducibility remains in band.

- Contract: are-scanner-api-v1@v1.3.0
- Scanner version: 0.5.0
- Corpus version: scoring golden corpus unchanged; log-event-schema-v1.0 for signed-wire payloads
- Probe-set version: post-bind-egress-wire-v1.0 / collector fixture-smoke
- Rubric point id: OPS.EVIDENCE_PIPELINE; OPS.VERSION_PINNING; post-bind monitor transport (non-scoring)
- N=5 summary: N=5 | baselined=8/9 | pending=1 | max ARE drift=0 | max delta vs baseline=0 | PASS
- CTO co-sign ref: null (rule-execution; BEAAA-7894 is the CTO-authored rule decision)
- Caveat: stock run_l1_n5 currently fails on legacy empty corpus_version pins; JSON records the explicit fallback used for B1/M1.

JSON evidence: tools/calibration-deltas/2026-06-23-beaaa7902-eddsa-signed-wire-n5.json

