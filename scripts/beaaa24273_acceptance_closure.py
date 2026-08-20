#!/usr/bin/env python3
"""Enumerate the acceptance test set for BEAAA-24273.

Three different numbers were quoted for "the tests that touch the registry"
during this ticket: 30/31 from a flat grep, 34/35 from a partial import walk,
and 60/61 from a full one. This script is the single definition. It prints, one
path per line, every test file that reaches ``tools.active_test_registry``,
``tools.canonical_test_registry`` or ``tools.probe_registry`` through any chain
of first-party imports, so the acceptance command means exactly one thing.

Usage:
    python3 scripts/beaaa24273_acceptance_closure.py [--root DIR]
    PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider --tb=line -rf \
        $(python3 scripts/beaaa24273_acceptance_closure.py | tr '\n' ' ')
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

TARGETS = {
    "tools.active_test_registry",
    "tools.canonical_test_registry",
    "tools.probe_registry",
}
# Every first-party import root, not just the ones the registry lives under. A
# test that reaches the registry only through ``api.full_scan_worker`` is in the
# set exactly as much as one that imports it directly.
SOURCE_ROOTS = (
    "tools",
    "harness",
    "api",
    "rubric",
    "scripts",
    "evals",
    "demo-real-agent",
)
TEST_ROOTS = ("tests", "demo-real-agent/tests")


def _module_index(root: Path) -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for base in SOURCE_ROOTS:
        base_path = root / base
        if not base_path.exists():
            continue
        for path in base_path.rglob("*.py"):
            parts = list(path.relative_to(root).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if not parts:
                continue
            modules.setdefault(".".join(parts), path)
            # demo-real-agent puts its own directory on sys.path, so its modules
            # are importable both as ``demo-real-agent.x`` and as ``x``.
            if parts[0] == "demo-real-agent" and len(parts) > 1:
                modules.setdefault(".".join(parts[1:]), path)
    return modules


def _imports(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def closure(root: Path) -> list[str]:
    """Modules that reach a target, by fixed point over the whole import graph.

    Deliberately not a memoised depth-first walk. Seeding a cycle guard with
    False and refining it after the recursion can cache a wrong False on an
    intermediate module whose truth depended on the guarded edge, which would
    silently drop tests from the acceptance set. A fixed point cannot do that:
    it only ever adds members, so it converges on the same answer whatever
    order the graph is walked in.
    """
    modules = _module_index(root)
    edges = {name: _imports(path) for name, path in modules.items()}

    reaching = set(TARGETS)
    changed = True
    while changed:
        changed = False
        for name, deps in edges.items():
            if name not in reaching and deps & reaching:
                reaching.add(name)
                changed = True

    hits: list[str] = []
    for test_root in TEST_ROOTS:
        base = root / test_root
        if not base.exists():
            continue
        for path in sorted(base.rglob("test_*.py")):
            if _imports(path) & reaching:
                hits.append(str(path.relative_to(root)))
    return sorted(set(hits))


def main() -> int:
    # ``--root`` lets one copy of this script enumerate the set on a tree that
    # does not carry it, such as the master worktree in the failed-set diff.
    argv = sys.argv[1:]
    if argv[:1] == ["--root"] and len(argv) == 2:
        root = Path(argv[1]).resolve()
    elif argv:
        raise SystemExit("usage: beaaa24273_acceptance_closure.py [--root DIR]")
    else:
        root = Path(__file__).resolve().parents[1]
    for path in closure(root):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
