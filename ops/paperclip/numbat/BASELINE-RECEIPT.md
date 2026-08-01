---
owner: CTO
artifact_type: release_evidence
status: final
updated_at: 2026-08-01T22:00:00Z
source_refs:
  - /BEAAA/issues/BEAAA-20244#comment-1ba2fdcd-9929-465a-b7c8-6c8cfa98bf59
  - /BEAAA/issues/BEAAA-19572#comment-337a1688-1c80-4d73-9ae0-c2f7de249b66
claim_or_decision: The exact P0-verified appliance 0.1.0 archive is published in the canonical Path B Scanner worktree with independently verified bytes and source-tree hashes.
verification: Archive SHA-256 and both manifest attachment hashes were independently recomputed; ZIP CRC passed; all 38 packaged source files match the supplied source-tree manifest. The Founder-delivered tree contained no Git metadata, so its source commit and Git tree are unavailable.
approvals: []
version: "1.0"
---

# Appliance 0.1.0 baseline receipt

- Archive: `backbond-appliance-0.1.0.zip`
- Archive bytes: `50251`
- Archive SHA-256: `f8d283d0013312c61faf9d4e4100b22167710cec8b7cf6a4f7e46079e2399e80`
- Unpacked packaged source: `backbond-appliance-0.1.0/backbond-appliance/`
- Packaged-source file count: `38`
- Packaged-source manifest: `backbond-appliance-0.1.0.source-tree.sha256`
- Packaged-source manifest SHA-256: `b54baf7f57ed428e1343dcc8d3badf8403e8a576563ed561060eef5853697623`
- Full on-box tree manifest: `backbond-appliance-0.1.0.tree.sha256`
- Full on-box tree manifest SHA-256: `44ebe1ad8584050482928c3a56f2923170a70920f1a7a238b6f625af08e298de`
- Provenance record: `backbond-appliance-0.1.0.provenance.json`

Source provenance is AriClaw `/root/backbond-appliance-0.1.0.zip`, with the delivered unpacked tree at `/root/backbond-appliance`. The source archive mtime is `2026-08-01T21:15:20.255685983Z`. No `.git` metadata was delivered, so a source commit and Git tree cannot be truthfully recorded.

The 17 additional files in the full on-box tree manifest are Python 3.12 `__pycache__` execution outputs. They are not packaged source and are intentionally excluded from the published unpacked baseline. Numbat records, raw-command logs, and captured content are excluded.
