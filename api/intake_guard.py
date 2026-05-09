"""BEAAA-22 §3 hosted-eval-only intake guard (Task D).

v0.1 accepts evidence only when each dim entry carries provenance proving it was
produced by the ARE hosted eval runner. Self-reported / customer-uploaded
results are rejected — that policy is enforced at the API edge so the scoring
engine never sees unverified raw scores.
"""
from __future__ import annotations

import re
from typing import Any

HOSTED_RUNNER = "beaai-hosted-eval"
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


class IntakeError(ValueError):
    pass


def enforce_hosted_eval_only(eval_outputs: dict[str, dict[str, Any]]) -> None:
    """Raise IntakeError on any dim entry that lacks hosted-runner provenance."""
    if not isinstance(eval_outputs, dict) or not eval_outputs:
        raise IntakeError("eval_outputs must be a non-empty mapping")
    for dim_id, entry in eval_outputs.items():
        if not isinstance(entry, dict):
            raise IntakeError(f"{dim_id}: entry must be an object")
        runner = entry.get("runner")
        if runner != HOSTED_RUNNER:
            raise IntakeError(
                f"{dim_id}: runner={runner!r} not allowed; v0.1 accepts hosted-eval only"
            )
        run_id = entry.get("eval_run_id")
        if not isinstance(run_id, str) or not _UUID_RE.match(run_id):
            raise IntakeError(f"{dim_id}: eval_run_id missing or not a UUID")
