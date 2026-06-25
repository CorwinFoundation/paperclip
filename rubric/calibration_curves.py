"""ARE Scanner calibration-curve registry — ADR-0027 (BEAAA-3161).

Resolves a named curve and applies the byte-source piecewise mapping to a
[0,1] rate to produce an ARE raw score in [0,4]. Three curves at registry
v1.0:

- ``CURVE_RUNNER_V1``           — ``scanners/promptfoo_runner.py:_pass_rate_to_raw``
- ``CURVE_LEGACY_TQA_FRONTIER`` — ``harness/bench/truthfulqa.py:_accuracy_to_raw``
- ``CURVE_LEGACY_EXFIL_PUNITIVE`` — ``harness/bench/secret_exfil.py:_resistance_to_raw``

The registry is the canonical source for calibration math; the original
bench/runner functions become thin wrappers that call ``get(curve_id).apply(rate)``.

Co-sign chain per ADR-0027: CTO (88e68019) + Underwriter operational (afa65747)
per ``contracts/scanner-implementation-supervision-v1.md@v1.0`` row "Change
scoring math". CEO ratification of ADR-0027: ``80db157b`` (2026-06-06).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_REGISTRY_PATH = Path(__file__).resolve().parent / "calibration_curves.yaml"

_DEFAULT_FALLBACK_ID = "CURVE_RUNNER_V1"


class CalibrationCurveError(ValueError):
    pass


@dataclass(frozen=True)
class CalibrationCurve:
    """Piecewise-constant calibration curve mapping rate→raw."""

    curve_id: str
    source: str
    version: str
    # Sorted descending by rate. Each row: (rate_cut, raw_value).
    mapping: tuple[tuple[float, float], ...]
    else_raw: float

    def apply(self, rate: float) -> float:
        """Map a [0,1] rate to a raw [0,4] score via the byte-source mapping.

        Walks the mapping top-down (descending cuts) and returns the first
        row whose ``rate_cut`` is <= the input. Returns ``else_raw`` when no
        row matches (rate strictly below the lowest cut).
        """
        for cut, raw in self.mapping:
            if rate >= cut:
                return raw
        return self.else_raw


_REGISTRY: dict[str, CalibrationCurve] | None = None


def _load_registry(path: Path | str | None = None) -> dict[str, CalibrationCurve]:
    src = Path(path) if path is not None else _REGISTRY_PATH
    data = yaml.safe_load(src.read_text())
    curves_raw = data.get("curves") or {}
    out: dict[str, CalibrationCurve] = {}
    for cid, spec in curves_raw.items():
        rows = spec.get("mapping") or []
        if not rows:
            raise CalibrationCurveError(f"curve {cid!r} has empty mapping")
        # Enforce strict descending order on rate so .apply() resolution is
        # unambiguous; surfaces a registry-edit error loudly rather than
        # silently miscalibrating at runtime.
        cuts = [float(r["rate"]) for r in rows]
        for prev, nxt in zip(cuts, cuts[1:]):
            if not prev > nxt:
                raise CalibrationCurveError(
                    f"curve {cid!r} mapping rows must be strictly descending "
                    f"by rate; saw {prev} then {nxt}"
                )
        mapping = tuple((float(r["rate"]), float(r["raw"])) for r in rows)
        out[cid] = CalibrationCurve(
            curve_id=cid,
            source=spec.get("source", ""),
            version=spec.get("version", ""),
            mapping=mapping,
            else_raw=float(spec.get("else_raw", 0.0)),
        )
    if _DEFAULT_FALLBACK_ID not in out:
        raise CalibrationCurveError(
            f"registry must define fallback curve {_DEFAULT_FALLBACK_ID!r}"
        )
    return out


def _registry() -> dict[str, CalibrationCurve]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _load_registry()
    return _REGISTRY


def resolve(curve_id: str | None) -> tuple[CalibrationCurve, dict[str, Any] | None]:
    """Resolve a curve and return a ScoreResponse diagnostic on fallback.

    ``get()`` preserves the historical stderr WARN behavior for direct callers.
    Scoring uses this resolver so a missing or unknown binding is also visible
    in ``ScoreResponse.diagnostics`` and the audit trail, not just stderr.
    """
    reg = _registry()
    if curve_id is None:
        return reg[_DEFAULT_FALLBACK_ID], {
            "code": "calibration_curve_fallback",
            "message": (
                "missing calibration curve binding; fallback "
                f"{_DEFAULT_FALLBACK_ID} applied"
            ),
            "detail": {
                "reason": "missing_curve_id",
                "requested_curve_id": None,
                "fallback_curve_id": _DEFAULT_FALLBACK_ID,
            },
        }
    curve = reg.get(curve_id)
    if curve is None:
        return reg[_DEFAULT_FALLBACK_ID], {
            "code": "calibration_curve_fallback",
            "message": (
                "unknown calibration curve binding; fallback "
                f"{_DEFAULT_FALLBACK_ID} applied"
            ),
            "detail": {
                "reason": "unknown_curve_id",
                "requested_curve_id": curve_id,
                "fallback_curve_id": _DEFAULT_FALLBACK_ID,
            },
        }
    return curve, None


def reload(path: Path | str | None = None) -> dict[str, CalibrationCurve]:
    """Reload the registry from disk. Test-only — production reads cached."""
    global _REGISTRY
    _REGISTRY = _load_registry(path)
    return _REGISTRY


def get(curve_id: str | None) -> CalibrationCurve:
    """Resolve a curve by id. Unknown/missing falls back to CURVE_RUNNER_V1
    with a stderr WARN — per AC#3, the engine never silently substitutes.
    """
    curve, diagnostic = resolve(curve_id)
    if diagnostic and diagnostic["detail"]["reason"] == "missing_curve_id":
        print(
            "[calibration_curves] WARN: missing curve_id; falling back to "
            f"{_DEFAULT_FALLBACK_ID}",
            file=sys.stderr,
        )
    elif diagnostic:
        print(
            f"[calibration_curves] WARN: unknown curve_id {curve_id!r}; "
            f"falling back to {_DEFAULT_FALLBACK_ID}",
            file=sys.stderr,
        )
    return curve


def apply(curve_id: str | None, rate: float) -> float:
    """Convenience: get(curve_id).apply(rate)."""
    return get(curve_id).apply(rate)


def all_curve_ids() -> list[str]:
    """List registered curve ids; useful for audit / tests."""
    return sorted(_registry().keys())


def registry_metadata() -> dict[str, Any]:
    """Curve metadata (source + version) keyed by curve_id; for ScoreResponse
    stamping if a downstream needs to record the curve binding at scan time.
    """
    return {
        cid: {"source": c.source, "version": c.version}
        for cid, c in _registry().items()
    }
