"""Isolated, default-off scanner bridge for Dynamic Score vNext.

The bridge intentionally has no dependency from the public response builders.
Callers receive a receipt path, never an augmented score response.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from dynamic_score_vnext import reference_calculator

SHADOW_PATH_VERSION = "backbond.dynamic-score-shadow-path@0.1.0"
PUBLIC_SCORE_KEYS = (
    "are_total",
    "band",
    "eligibility",
    "pricing",
    "premium",
    "quote",
    "policy",
    "warranty",
    "underwriting_decision",
)


class ShadowPathError(ValueError):
    """Raised when the shadow boundary or implementation pin is invalid."""


def calculator_sha256() -> str:
    """Return the exact SHA-256 of the calculator source used by this process."""
    source = Path(reference_calculator.__file__).read_bytes()
    return hashlib.sha256(source).hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _write_once(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != body:
            raise ShadowPathError(f"receipt collision at {path}")
        return
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != body:
                raise ShadowPathError(f"receipt collision at {path}")
    finally:
        temporary.unlink(missing_ok=True)


def run_shadow(
    *,
    public_result: Mapping[str, Any],
    evidence_payload: Mapping[str, Any],
    receipt_directory: str | Path,
    expected_calculator_sha256: str,
    enabled: bool = False,
) -> Path | None:
    """Run vNext without changing or decorating the public scan result.

    ``enabled`` is explicit and defaults off. The baseline score is always
    copied from the completed public result so a caller cannot substitute a
    different score in the shadow input.
    """
    if not enabled:
        return None

    implementation_sha256 = calculator_sha256()
    if implementation_sha256 != expected_calculator_sha256:
        raise ShadowPathError(
            "calculator SHA-256 mismatch: "
            f"expected {expected_calculator_sha256}, got {implementation_sha256}"
        )

    if "are_total" not in public_result:
        raise ShadowPathError("completed public_result must contain are_total")

    baseline = {key: public_result.get(key) for key in PUBLIC_SCORE_KEYS if key in public_result}
    calculator_input = dict(evidence_payload)
    supplied_score = calculator_input.pop("current_score", None)
    if supplied_score is not None and str(supplied_score) != str(public_result["are_total"]):
        raise ShadowPathError("shadow current_score must match public are_total")
    calculator_input["current_score"] = public_result["are_total"]
    calculator_input["calculator_code_sha256"] = implementation_sha256

    shadow_output = reference_calculator.calculate(calculator_input)
    envelope: dict[str, Any] = {
        "schema": SHADOW_PATH_VERSION,
        "mode": "shadow_only",
        "calculator_model_version": reference_calculator.MODEL_VERSION,
        "calculator_sha256": implementation_sha256,
        "parameters_sha256": reference_calculator.PARAMETERS_SHA256,
        "model_sha256": reference_calculator.MODEL_SHA256,
        "sink": "isolated_content_addressed_receipt",
        "public_baseline": baseline,
        "calculator_input": calculator_input,
        "shadow_output": shadow_output,
    }
    receipt_sha256 = hashlib.sha256(_canonical_json(envelope)).hexdigest()
    envelope["receipt_sha256"] = receipt_sha256
    body = json.dumps(envelope, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
    destination = Path(receipt_directory) / f"{receipt_sha256}.json"
    _write_once(destination, body)
    return destination
