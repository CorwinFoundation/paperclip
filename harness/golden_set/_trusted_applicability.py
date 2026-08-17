"""Trusted applicability loading for the frozen golden-set replay entrypoints.

BEAAA-23752. The golden-set entrypoints (`calibration_delta`, `n5_band`,
`discrimination`) replay locked fixtures that carry `fires: false` waivers in
their `applicability.json` sidecars. Since BEAAA-18082, `rubric.scoring.score()`
refuses to honour N/A waivers from a raw dict — a harness that reads a sidecar
off disk and hands it straight to `score()` is exactly the forgery path that
boundary closed.

The sanctioned route for this case is `harness.trusted_replay`, which
authenticates the sidecar against a code-pinned manifest digest before signing
it. Routing all three entrypoints through this one helper keeps that check in a
single place rather than triplicating a security-relevant call.

Failure is closed: a tampered sidecar, an unpinned fixture, or a manifest that
does not match `PINNED_MANIFEST_SHA256` raises `TrustedReplayError` rather than
degrading to an unvalidated replay.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from harness.trusted_replay import validate_trusted_replay
from rubric.scoring import ValidatedApplicability

TRUSTED_REPLAY_MANIFEST = "trusted-replay-manifest-v1.json"


def load_trusted_applicability(
    baselines: Path, fixture_id: str
) -> ValidatedApplicability | None:
    """Return authenticated applicability for `fixture_id`, or None if absent.

    `fixture_id` is passed as both the fixture and the agent identity: the
    golden-set replay scores fixture X's locked outputs as agent X, and
    `validate_trusted_replay` fails closed if those disagree.
    """
    app_path = baselines / fixture_id / "applicability.json"
    if not app_path.exists():
        return None
    raw = app_path.read_bytes()
    return validate_trusted_replay(
        manifest_path=baselines / TRUSTED_REPLAY_MANIFEST,
        fixture_id=fixture_id,
        agent_id=fixture_id,
        applicability_sha256=hashlib.sha256(raw).hexdigest(),
        decisions=json.loads(raw.decode("utf-8")).get("applicability_decisions") or {},
    )
