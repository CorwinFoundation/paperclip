from __future__ import annotations

import ast
from pathlib import Path


TRACKER_SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "log_tracker"
FORBIDDEN_ROOTS = {"bands", "pricing", "rubric", "scoring"}
FORBIDDEN_DOTTED_PREFIXES = ("tools.portal_v1",)


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
                if node.module == "tools":
                    imports.extend(f"tools.{alias.name}" for alias in node.names)
            elif node.level == 0:
                imports.extend(alias.name for alias in node.names)
    return imports


def _is_forbidden(module_name: str) -> bool:
    root = module_name.split(".", 1)[0]
    return root in FORBIDDEN_ROOTS or any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in FORBIDDEN_DOTTED_PREFIXES
    )


def test_log_tracker_does_not_import_scoring_rubric_pricing_or_portal() -> None:
    offenders: list[str] = []
    for path in sorted(TRACKER_SRC_DIR.rglob("*.py")):
        for module_name in _imported_modules(path):
            if _is_forbidden(module_name):
                offenders.append(f"{path.relative_to(TRACKER_SRC_DIR)}:{module_name}")

    assert offenders == []


def test_firewall_predicate_is_exact_to_forbidden_source_packages() -> None:
    forbidden = [
        "bands",
        "bands.cuts",
        "pricing.rates",
        "rubric.points",
        "scoring.engine",
        "tools.portal_v1",
        "tools.portal_v1.post_bind_sinks_v0_1",
    ]
    allowed = [
        "log_tracker.shared_pricing_labels",
        "post_bind_ingest.sinks.common",
        "tools.observability_compat",
    ]

    assert [_is_forbidden(module_name) for module_name in forbidden] == [True] * len(
        forbidden
    )
    assert [_is_forbidden(module_name) for module_name in allowed] == [False] * len(
        allowed
    )
