"""Auditor import-edge check: no imports from score-yielding modules.

Per ADR-0034 §9 reuse map: the post-bind ingest package may reuse pre-bind
probe substrate at the helper / shape level by copy-and-adapt, NOT by runtime
import. This test walks every module under `src/post_bind_ingest/` and asserts
that no import statement references the following forbidden roots:

  - tools.portal_v1            (BEAAA-5761 rollup view + scoring entrypoints)
  - tools.are_connector        (BEAAA-5754 ProbeRuntime + redaction substrate)
  - tools.are-connector        (filesystem form)
  - src.scanner_v0_3_2         (BEAAA-2799 scanner core)
  - api.*                      (FastAPI / route handlers carrying score state)
  - scanners.*                 (scanner pillars)
  - rubric.*                   (rubric weights and dimension definitions)

This is the non-negotiable Δ=0 attestation per CTO brief §5 AC#5. A future
contributor adding an import to any of these roots breaks the test; the fix is
to refactor into a copy-and-adapt helper, not to add the import.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from _test_paths import PACKAGE_ROOT

SRC_ROOT = PACKAGE_ROOT / "src" / "post_bind_ingest"

FORBIDDEN_PREFIXES = (
    "tools.portal_v1",
    "tools.are_connector",
    "tools.are-connector",
    "src.scanner_v0_3_2",
    "scanner_v0_3_2",
    "api",
    "api.",
    "scanners",
    "scanners.",
    "rubric",
    "rubric.",
    # The forbidden list also includes the connector package name itself in
    # case a contributor symlinks it onto sys.path. The byte-equality test
    # (`test_redaction_drift_vs_connector`) reads the file via filesystem
    # path, not via import — so this prefix may not appear under runtime.
    "are_connector",
)


def _iter_python_files() -> list[Path]:
    return [p for p in SRC_ROOT.rglob("*.py") if p.is_file()]


def _imports_in(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            # Relative imports (level > 0) cannot reach forbidden roots
            # outside the package; skip them.
            if node.level > 0:
                continue
            out.append((module, node.lineno))
    return out


def _is_forbidden(module: str) -> bool:
    for prefix in FORBIDDEN_PREFIXES:
        if module == prefix or module.startswith(prefix + "."):
            return True
    return False


def test_no_module_imports_forbidden_roots() -> None:
    violations: list[tuple[Path, str, int]] = []
    for path in _iter_python_files():
        for module, lineno in _imports_in(path):
            if _is_forbidden(module):
                violations.append((path, module, lineno))
    if violations:
        msg_lines = ["forbidden imports detected (ADR-0034 §9 import-edge violation):"]
        for path, module, lineno in violations:
            rel = path.relative_to(PACKAGE_ROOT)
            msg_lines.append(f"  {rel}:{lineno} imports {module!r}")
        pytest.fail("\n".join(msg_lines))


def test_runtime_import_does_not_load_forbidden_modules() -> None:
    """Belt-and-braces: importing the package must not transitively load forbidden modules."""
    pre = set(sys.modules)
    import post_bind_ingest  # noqa: F401
    from post_bind_ingest import normalizer  # noqa: F401
    from post_bind_ingest import ingest_gateway  # noqa: F401
    from post_bind_ingest import score_invariants  # noqa: F401

    loaded_after = set(sys.modules) - pre
    for module_name in loaded_after:
        assert not _is_forbidden(module_name), (
            f"package import transitively loaded forbidden module {module_name!r}"
        )
