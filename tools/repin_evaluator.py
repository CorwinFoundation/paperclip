#!/usr/bin/env python3
"""Refresh the tranche-1 evaluator pin from its authoritative source file."""

from __future__ import annotations

import argparse
import ast
import hashlib
import re
from pathlib import Path


EXPECTED_FAMILIES = {
    "tool_misuse_param_tamper",
    "tool_misuse_scope_escape",
    "tool_misuse_unauthorized_call",
}
CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")
SHA256_LITERAL = re.compile(r'"[0-9a-f]{64}"')


def _read_resolved(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if any(marker in text for marker in CONFLICT_MARKERS):
        raise RuntimeError(f"refusing to repin with unresolved conflict markers: {path}")
    return text


def repin(repo_root: Path) -> tuple[str, str]:
    evaluator_path = repo_root / "tools" / "registered_case_executor.py"
    pin_path = repo_root / "tools" / "tranche1_observational.py"
    evaluator_bytes = evaluator_path.read_bytes()
    if any(marker.encode() in evaluator_bytes for marker in CONFLICT_MARKERS):
        raise RuntimeError(
            f"refusing to repin with unresolved conflict markers: {evaluator_path}"
        )
    new_pin = hashlib.sha256(evaluator_bytes).hexdigest()
    source = _read_resolved(pin_path)
    tree = ast.parse(source, filename=str(pin_path))
    assignment = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "EVALUATOR_SHA256"
                for target in node.targets
            )
        ),
        None,
    )
    if assignment is None or not isinstance(assignment.value, ast.Dict):
        raise RuntimeError("EVALUATOR_SHA256 must be a top-level dictionary assignment")
    families = {
        key.value
        for key in assignment.value.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    values = {
        value.value
        for value in assignment.value.values
        if isinstance(value, ast.Constant) and isinstance(value.value, str)
    }
    if families != EXPECTED_FAMILIES or len(values) != 1:
        raise RuntimeError("EVALUATOR_SHA256 has an unexpected family or value shape")
    old_pin = next(iter(values))
    lines = source.splitlines(keepends=True)
    start = assignment.lineno - 1
    end = assignment.end_lineno
    block = "".join(lines[start:end])
    rewritten, count = SHA256_LITERAL.subn(f'"{new_pin}"', block)
    if count != len(EXPECTED_FAMILIES):
        raise RuntimeError("EVALUATOR_SHA256 must contain exactly three SHA-256 literals")
    lines[start:end] = [rewritten]
    pin_path.write_text("".join(lines), encoding="utf-8")
    return old_pin, new_pin


def main() -> int:
    argparse.ArgumentParser(
        description="Derive EVALUATOR_SHA256 from registered_case_executor.py."
    ).parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    old_pin, new_pin = repin(repo_root)
    print(f"EVALUATOR_SHA256: {old_pin} -> {new_pin}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
