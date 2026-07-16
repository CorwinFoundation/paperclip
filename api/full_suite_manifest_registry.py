"""Active-registry adapters for full-suite execution manifests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from api.full_suite_manifest_models import FullSuiteCase, FullSuiteManifestError
from rubric.scoring import Rubric


DEFAULT_ACTIVE_REGISTRY_PATH = (
    Path(__file__).resolve().parents[1] / "tools" / "probe-registry" / "active_registry.json"
)


def active_registry_from_rubric(rubric: Rubric) -> tuple[FullSuiteCase, ...]:
    return tuple(
        FullSuiteCase(case_id=dim_id, rubric_point_id=dim_id, required=True)
        for dim_id in rubric.active_dim_ids
    )


def active_registry_from_manifest(registry: Mapping[str, Any]) -> tuple[FullSuiteCase, ...]:
    points = registry.get("active_rubric_points")
    if points is None and "rows" in registry:
        return active_registry_from_canonical_registry(registry)
    if not isinstance(points, list) or not points:
        raise FullSuiteManifestError("active_registry.active_rubric_points must be non-empty")
    cases: list[FullSuiteCase] = []
    seen: set[str] = set()
    for point in points:
        if not isinstance(point, Mapping):
            raise FullSuiteManifestError("active_registry.active_rubric_points rows must be objects")
        rubric_point_id = point.get("rubric_point_id")
        if not isinstance(rubric_point_id, str) or not rubric_point_id:
            raise FullSuiteManifestError("active registry point missing rubric_point_id")
        if rubric_point_id in seen:
            raise FullSuiteManifestError(f"duplicate active registry point: {rubric_point_id}")
        seen.add(rubric_point_id)
        cases.append(FullSuiteCase(rubric_point_id, rubric_point_id, required=True))
    return tuple(cases)


def active_registry_from_canonical_registry(registry: Mapping[str, Any]) -> tuple[FullSuiteCase, ...]:
    derived_views = registry.get("derived_views")
    if isinstance(derived_views, Mapping):
        active_view = derived_views.get("active_registry")
        if isinstance(active_view, Mapping):
            return active_registry_from_manifest(active_view)

    rows = registry.get("rows")
    if not isinstance(rows, list) or not rows:
        raise FullSuiteManifestError("canonical registry rows must be non-empty")
    cases: list[FullSuiteCase] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise FullSuiteManifestError("canonical registry rows must be objects")
        if (
            row.get("disposition") != "production_active"
            or row.get("countable_as_active_executable") is not True
        ):
            continue
        rubric_point_id = row.get("rubric_point_id")
        if not isinstance(rubric_point_id, str) or not rubric_point_id or rubric_point_id == "n/a":
            continue
        if rubric_point_id in seen:
            continue
        seen.add(rubric_point_id)
        cases.append(FullSuiteCase(rubric_point_id, rubric_point_id, required=True))
    if not cases:
        raise FullSuiteManifestError("canonical registry has no active executable rubric points")
    return tuple(sorted(cases, key=lambda case: case.rubric_point_id))


def active_registry_from_path(
    path: Path | str = DEFAULT_ACTIVE_REGISTRY_PATH,
) -> tuple[FullSuiteCase, ...]:
    registry_path = Path(path)
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FullSuiteManifestError(f"active registry not found: {registry_path}") from exc
    except json.JSONDecodeError as exc:
        raise FullSuiteManifestError(
            f"active registry is invalid JSON: {registry_path}: {exc}"
        ) from exc
    if not isinstance(registry, Mapping):
        raise FullSuiteManifestError("active registry root must be an object")
    return active_registry_from_manifest(registry)
