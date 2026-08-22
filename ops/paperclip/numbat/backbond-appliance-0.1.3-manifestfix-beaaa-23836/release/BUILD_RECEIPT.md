# BEAAA-21472 appliance 0.1.3 exact-successor receipt

BLUF: This candidate supersedes package
`84e5c1cc6c04ebe6eca3959318971f55d927b774d2740f6771972a261ad660aa`,
which Designer validation blocked on BEAAA-21469. Every stable `BB-APPL-*`
failure now emits `ERROR`, verified `SAFE`, specific `CHECK`, bounded `FIX`, and
redacted `HELP` lines on stderr before exiting non-zero.

- Classification: rule-execution under `scanner-implementation-supervision-v1@v1.1`.
- Contract anchor: `are-scanner-api-v1@v1.3.0`; output shape unchanged.
- Base image: `python:3.11-slim@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff`.
- Python graph: 12 direct/transitive packages, exact versions and verified hashes.
- Runtime: UID 10001, read-only rootfs, all capabilities dropped,
  `no-new-privileges`, bounded tmpfs/CPU/memory/PIDs, read-only substrate/config,
  one writable named state volume.
- Distribution: compressed `docker save` OCI-compatible image archive plus exact
  Docker content ID and SHA-256 manifest. No host Python or pip is used.
- SBOM: SPDX 2.3 at `release/sbom.spdx.json`.
- Upgrade/rollback: `install.sh` retains current/prior immutable image IDs and
  preserves state; destructive state-volume deletion is excluded.
- Test command: `./run_all_tests.sh` on host and inside the read-only bounded image.
- Error-path smoke: unsupported action produced exactly five stderr lines, empty
  stdout, and a non-zero exit; the package gate covers recovery mappings 001-012.
- Shell validation: `bash -n` passed. ShellCheck was unavailable in the build
  environment and remains an explicit downstream validation item.
- Fixture boundary: synthetic fixtures only; no raw Numbat records or external data.
- Scanner metadata: scanner/corpus/probe/rubric score surfaces not changed;
  controlled N=5 score delta `0.0000`.
- External scope: partner-facing. Design basis: BEAAA-21409 v1.0. Brand Backbone
  v1.6 pin verified at `a2ffc4f919b0f71a17b9f4506ac4edf11cd37956ff494f00d7a45f0b166ea090`.
  Final exact-candidate
  Designer, CMO, CTO/product, and QA gates remain required.

No deployment or production promotion is authorized by this receipt.
