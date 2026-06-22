from __future__ import annotations

import ast
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "post_bind_ingest"
SINKS_DIR = PACKAGE_DIR / "sinks"
FORBIDDEN_IMPORT_ROOTS = {"portal", "rubric", "scoring", "scanner", "scanners"}


def test_post_bind_sinks_do_not_import_scoring_or_portal_modules() -> None:
    offenders: list[str] = []
    for path in sorted(SINKS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".", 1)[0]]
            else:
                continue
            for name in names:
                if name in FORBIDDEN_IMPORT_ROOTS:
                    offenders.append(f"{path.name}:{name}")
    assert offenders == []


def test_dogfood_tap_does_not_import_scoring_or_pricing_modules() -> None:
    path = PACKAGE_DIR / "dogfood_tap.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name.split(".", 1)[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module.split(".", 1)[0]]
        else:
            continue
        for name in names:
            if name in FORBIDDEN_IMPORT_ROOTS:
                offenders.append(f"{path.name}:{name}")
    assert offenders == []
