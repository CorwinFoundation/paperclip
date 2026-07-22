"""Pinned, fixture-scoped trust boundary for frozen golden calibration replay."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from api.bundle import _signing_key
from rubric.scoring import ValidatedApplicability


PINNED_MANIFEST_SHA256 = "14f3a556a6128424e9791a2493c68dab54bc9441ef4f5736684fea17d4987220"
MAX_MANIFEST_BYTES = 64 * 1024


class TrustedReplayError(ValueError):
    """The requested frozen replay identity or evidence is not trusted."""


def validate_trusted_replay(
    *,
    manifest_path: Path,
    fixture_id: str,
    agent_id: str,
    applicability_sha256: str | None,
    decisions: dict[str, dict[str, Any]],
) -> ValidatedApplicability:
    raw = manifest_path.read_bytes()
    if len(raw) > MAX_MANIFEST_BYTES:
        raise TrustedReplayError("trusted replay manifest exceeds size limit")
    actual_manifest_sha = hashlib.sha256(raw).hexdigest()
    if actual_manifest_sha != PINNED_MANIFEST_SHA256:
        raise TrustedReplayError(
            "trusted replay manifest digest is not the code-pinned digest"
        )
    try:
        manifest = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrustedReplayError("trusted replay manifest is invalid JSON") from exc
    if manifest.get("version") != "1.0.0" or manifest.get("purpose") != "frozen_golden_calibration_replay":
        raise TrustedReplayError("trusted replay manifest type/version rejected")
    if fixture_id != agent_id:
        raise TrustedReplayError(
            f"trusted replay fixture identity {fixture_id!r} does not match agent {agent_id!r}"
        )
    entry = (manifest.get("fixtures") or {}).get(fixture_id)
    if not isinstance(entry, dict):
        raise TrustedReplayError(f"fixture identity {fixture_id!r} is not pinned")
    expected_sha = entry.get("applicability_sha256")
    normalized_sha = (applicability_sha256 or "").removeprefix("sha256:")
    if normalized_sha != expected_sha:
        raise TrustedReplayError(
            f"trusted replay applicability digest mismatch for fixture {fixture_id!r}"
        )
    return ValidatedApplicability._signed_from_policy_validation(
        decisions, f"trusted-replay-manifest@{manifest['version']}", _signing_key()
    )
