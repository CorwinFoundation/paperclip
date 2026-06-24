# ARE Probe SDK — R2 internal live-target shim

**Internal-only.** Under Founder comms-freeze per `cto-internal-directive-2026-06-13` §3.
No distribution, no public repo, no DP onboarding outside this workspace until the Founder
lifts the freeze.

## What this is

`are-probe` is the design-partner-side companion to the BackBond Scanner.
A DP runs `are-probe --target <agent-url> --config are-probe.yaml` against
their own deployed agent; the SDK runs a pinned probe corpus, captures
responses, scores them against the canonical rubric via the master
`bin/are-scan` CLI (wrapped, not forked), and emits a v1.0 envelope
conforming to `contracts/are-scanner-api-v1.md@v1.0`.

No telemetry leaves the DP machine. The envelope stays local until the DP
hands it back to BackBond over an out-of-band channel.

## R2 internal scope

- CLI `are-probe` end-to-end against an in-process fixture target and
  self-owned live targets that can be normalized by the generic shim.
- One probe family: refusal-robustness (DoNotAnswer-style; deterministic
  keyword graders). Functional-use only for BEAAA-8037.
- Target shapes:
  - `generic_prompt_output`: POST `{"prompt":"..."}` -> `{"output":"..."}`.
  - `botpress_webchat`: POST webchat-style message payload and extract text
    from common Botpress response shapes.
  - `transcript_export`: read a local JSON/JSONL transcript as Path B fallback.
- Portal CA signature stub (Ed25519 over canonical-JSON envelope bytes;
  unsigned-by-default mode with a `--sign` opt-in).
- SHA-pinned lockfile covering Python deps, corpus packs, scanner master
  ref, and model client lib refs.
- N=5 byte-identical scores on fixture target (spread=0 on are_total and
  every pillar subscore).

## Explicitly out of scope

- DP-signed envelopes.
- OpenAI / Anthropic / OpenRouter HTTP client adapters beyond the generic
  bearer-token surface.
- Public corpus packs.
- Distribution surface: no PyPI publish, no public repo, no DP doc.
  See `DISTRIBUTION_READINESS_CHECKLIST.md` for distribution prerequisites.

## Layout

```
engineering/probe-sdk/
├── README.md                       # this file
├── beaaa-5056-r1-build-v0.1.md     # R1 historical workspace artifact
├── DISTRIBUTION_READINESS_CHECKLIST.md  # R2 prerequisites (NOT actioned)
├── pyproject.toml                  # package metadata + deps
├── lockfile.toml                   # SHA-pinned deps + corpus + scanner ref
├── src/are_probe/                  # package source
├── config/are-probe.yaml.example   # example config
├── corpus/                         # internal-only probe corpus packs
├── fixtures/                       # in-process fixture target + corpus
├── bin/are-probe                   # CLI launcher
└── tests/                          # pytest suite (incl N=5 determinism)
```

## Reproducibility

`build_hash` in `lockfile.toml` is `sha256(canonical_json(pinned_versions))`.
N=5 determinism: same target + same config + same R2 build_hash + same seed
→ byte-identical `are_total` and pillar subscores. Envelope `run_timestamp`
is the one field allowed to vary; tests project it out.

## Running (internal only)

```
# Bootstrap (assumes scanner master at $ARE_SCAN_BIN)
export ARE_SCAN_BIN=/mnt/paperclipdata/dot-paperclip/instances/default/workspaces/d8bbc3b5-185f-43ed-bff0-d00323ddbfa3/are-scanner/bin/are-scan
python3 -m are_probe.cli \
    --target http://127.0.0.1:18077 \
    --config config/are-probe.yaml.example \
    --out /tmp/r2-envelope.json
```
