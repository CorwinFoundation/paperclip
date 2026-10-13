# BEAAA-35699 acceptance reconciliation

Status: proposed for Ari review before the Botpress live batch.

## Exact acceptance wording

The preserved Syllable cohort consists of five independently executed, scored `are-scanner-api-v1@v1.3.0` envelopes. Each carries `are_total`, four pillar subscores, confidence `1.0`, 27 evidence references, scanner/corpus/rubric/model versions, seed, and distinct `run_id` and `snapshot_hash` values. These are the five previously accepted Syllable scored envelopes recorded on BEAAA-35669 in comment `88e85dd7-973e-4f0b-abdb-e8d7709c217d`:

1. Syllable scored envelope 1 of 5.
2. Syllable scored envelope 2 of 5.
3. Syllable scored envelope 3 of 5.
4. Syllable scored envelope 4 of 5.
5. Syllable scored envelope 5 of 5.

The proposed Botpress cohort consists of five independently executed public-demo evidence envelopes. Each must carry a functional bounded reply, `run_id`, `snapshot_hash`, scanner/corpus/rubric/model versions, seed, provenance, and reachable evidence references. Because the public demo provides partial coverage, each Botpress envelope must carry `status: reported_not_scored`, `score_input: false`, and N/A values for total score, subscores, band, and premium. The five Botpress envelopes are evidence runs, not complete scored customer-account scans.

Acceptance may state: **five scored Syllable envelopes plus five independent report-only public Botpress evidence envelopes**. It must not state that the mixed cohort is ten complete scored customer-account scans. The original numeric ten-run acceptance criterion remains unmet unless five additional eligible scored envelopes are produced under complete coverage.

The companion fixture `public-botpress-report-only-fixture.json` demonstrates the proposed Botpress envelope boundary. Its values are explicitly synthetic and are not live-run evidence.
