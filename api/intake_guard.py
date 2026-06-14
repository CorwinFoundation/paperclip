"""BEAAA-22 §3 hosted-eval-only intake guard (Task D).

v0.1 accepts evidence only when each dim entry carries provenance proving it was
produced by the ARE hosted eval runner. Self-reported / customer-uploaded
results are rejected — that policy is enforced at the API edge so the scoring
engine never sees unverified raw scores.

v0.1.4 additive (BEAAA-163 Option C/B+C tamper-resistance pass): scanner
inputs are themselves an adversarial surface — a scanned agent's output can
flow into eval_outputs and attempt to inject downstream consumers (forensic
bundle reader, report-card renderer, log pipeline). enforce_input_safety()
is defense-in-depth on top of provenance: it bounds string sizes, rejects
control characters, enforces https:// on evidence_uri, validates
evidence_sha256 shape, and rejects non-numeric raw smuggling.
"""
from __future__ import annotations

import re
from typing import Any

HOSTED_RUNNER = "beaai-hosted-eval"
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$", re.IGNORECASE)
# Two non-overlapping shapes: legacy `P<n>.<m>` (rubric v1.0.0) and fully-qualified
# `Pillar.FAMILY.SUB` (rubric v1.0.1, strict 3-segment all-caps). ADR-0027 (BEAAA-2198).
_DIM_ID_RE = re.compile(
    r"^(?:P\d+\.\d+|[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*)$"
)
_EVIDENCE_URI_MAX = 2048
_DIM_ID_MAX = 64
_MAX_DIMS = 64

# Control chars: anything below 0x20 except tab/newline/cr; plus DEL (0x7F).
# Newline/tab in evidence_uri or evidence_sha256 is still rejected (URIs
# don't legitimately contain them); the broader check is for any string
# field that could be log-injection or terminal-escape vector.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1F\x7F]")


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


def enforce_input_safety(eval_outputs: dict[str, dict[str, Any]]) -> None:
    """Belt-and-suspenders defense in depth on adversarial scanner inputs.

    Runs alongside enforce_hosted_eval_only at the API edge. Provenance
    establishes WHO produced the eval; this function validates WHAT was
    produced is shape-safe to flow into the scoring engine, forensic
    bundle, and report-card renderer.

    Rejects, with an explicit error per case:
      - eval_outputs map larger than _MAX_DIMS entries (DoS surface)
      - dim_id that doesn't match the canonical Pn.m shape
      - dim_id longer than _DIM_ID_MAX
      - raw that's not int/float (no string-coerced or list-wrapped values)
      - evidence_uri scheme other than https:// (or empty)
      - evidence_uri longer than _EVIDENCE_URI_MAX
      - evidence_sha256 not matching sha256:<64-hex> (or empty)
      - control characters or DEL in any string field
    """
    if not isinstance(eval_outputs, dict):
        raise IntakeError("eval_outputs must be a mapping")
    if len(eval_outputs) > _MAX_DIMS:
        raise IntakeError(
            f"eval_outputs has {len(eval_outputs)} entries; max allowed is {_MAX_DIMS}"
        )

    for dim_id, entry in eval_outputs.items():
        if not isinstance(dim_id, str) or len(dim_id) > _DIM_ID_MAX:
            raise IntakeError(f"dim_id={dim_id!r} too long or not a string")
        if not _DIM_ID_RE.match(dim_id):
            raise IntakeError(f"dim_id={dim_id!r} does not match canonical Pn.m shape")
        if not isinstance(entry, dict):
            raise IntakeError(f"{dim_id}: entry must be an object")

        raw = entry.get("raw")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise IntakeError(
                f"{dim_id}: raw must be a numeric type, got {type(raw).__name__}"
            )

        for field_name in ("evidence_uri", "evidence_sha256", "runner", "eval_run_id"):
            value = entry.get(field_name)
            if value is None:
                continue
            if not isinstance(value, str):
                raise IntakeError(
                    f"{dim_id}.{field_name} must be a string, got {type(value).__name__}"
                )
            if _CONTROL_CHARS_RE.search(value):
                raise IntakeError(
                    f"{dim_id}.{field_name} contains control characters"
                )

        evidence_uri = entry.get("evidence_uri", "")
        if evidence_uri:
            if len(evidence_uri) > _EVIDENCE_URI_MAX:
                raise IntakeError(
                    f"{dim_id}.evidence_uri exceeds {_EVIDENCE_URI_MAX} bytes"
                )
            if not evidence_uri.startswith("https://"):
                raise IntakeError(
                    f"{dim_id}.evidence_uri must be https:// (got scheme prefix "
                    f"{evidence_uri.split(':', 1)[0]!r})"
                )

        evidence_sha256 = entry.get("evidence_sha256", "")
        if evidence_sha256 and not _SHA256_RE.match(evidence_sha256):
            raise IntakeError(
                f"{dim_id}.evidence_sha256 must match sha256:<64-hex>"
            )
