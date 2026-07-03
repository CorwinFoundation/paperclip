"""BEAAA-11372 Determination Engine v1.

Turns immutable runtime evidence (Evidence Profile v1 events persisted in the
append-only evidence store, BEAAA-11366) into reproducible covered-peril
determination records for the scoped wrapper-1 perils, with no LLM in the
deciding path.

Governing spec: `backbond-runtime-evidence-pipeline-build-spec-2026-07-02` §3.4.B
(BEAAA-11341); parent execution lane BEAAA-11371. Operates under
`contracts/are-scanner-api-v1.md@v1.3.0` and `contracts/evidence-profile-v1.md`.
This package adds runtime evidence adjudication infrastructure only; it does not
change BackBond Score math, rubric weights, judge models, or the
`/scanner/v1/scans` response envelope.
"""
from __future__ import annotations

DETERMINATION_ENGINE_VERSION = "determination-engine@1.0.0"

__all__ = ["DETERMINATION_ENGINE_VERSION"]
