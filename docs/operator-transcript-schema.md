# Operator transcript schema v0.2

Status: **v0.2 — default**. Contract: `contracts/are-scanner-api-v1.md@v1.3.0`.

Run `python3 scripts/score_transcript.py --transcript-from-json INPUT.json --out-dir OUTPUT`.
The former `--experimental` gate is removed.

The envelope schema remains `are-scanner.operator-transcript.v1` and requires
`scanner_version`, `corpus_version`, pinned subject/judge `model_versions`, an
integer `seed`, and a non-empty `responses` array. Each response requires
`turn_index`, `dim`, `probe_id`, `prompt`, and `content`.

Supported dimensions are P1.1, P1.2, P1.3, P1.5, P2.1, P3.1, P3.2, and P3.3.
P1.3 additionally requires `variant_responses`, each containing `prompt`,
`temperature` (0.0, 0.3, or 0.7), and `content`. P1.5 content carries the
confidence value on its final line. P2.1 content carries the selected answer.

Malformed, oversized (>10 MB), duplicate, unknown-dimension, or invalid nested
input exits with code 4 and writes no partial score artifact.
