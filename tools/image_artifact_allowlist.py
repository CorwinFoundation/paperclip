"""BEAAA-35637: keep the image's ``artifacts/`` allowlist in step with the code.

``.dockerignore`` denies ``artifacts/*`` and re-includes evidence files one at a
time. Nothing tied that allowlist to the code, so a module could start reading a
new evidence file and the file would simply not be in the image. BEAAA-35634 hit
exactly that: ``artifacts/beaaa-35380/tool-misuse-dev-activation-ledger.json``
was read at runtime, absent from the image, and the read returned ``{}`` without
a word, so twelve ``tool_misuse_param_tamper`` rows shipped with no execution
evidence and every repo-tree check stayed green.

This module is the durable half of the fix. :data:`RUNTIME_ARTIFACT_INVENTORY`
records every ``artifacts/`` path the live runtime names and what it does with
it; :func:`audit` checks each ``read_required`` entry against the real
``.dockerignore``; :func:`scan_runtime_artifact_literals` re-derives the set from
source so a new, undeclared path fails the test rather than the release gate.

The runtime surface is the transitive first-party import closure of
``scripts/verify_runtime_import_closure.py``'s ``LIVE_MODULES`` — the same list
the Docker build already enforces, so the two cannot drift apart.
"""
from __future__ import annotations

import ast
import json
import importlib.util
import re
import sys
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Iterable, Iterator


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERIGNORE_PATH = REPO_ROOT / ".dockerignore"

#: How the runtime uses each ``artifacts/`` path it names.
#:
#: ``read_required``   opened at runtime; absence is a defect, must be in the image.
#: ``read_optional``   opened at runtime; absence is a documented, logged skip.
#: ``output``          written, not read; the image never needs it shipped.
#: ``reference_only``  carried as an evidence-reference or corpus-source string,
#:                     never opened.
RUNTIME_ARTIFACT_INVENTORY: dict[str, str] = {
    "artifacts/BEAAA-25646/c10-mapping-ledger-v1.0.jsonl": "read_required",
    "artifacts/BEAAA-26529": "read_required",
    "artifacts/BEAAA-33199/capability-manifest.json": "read_required",
    "artifacts/beaaa-152/datasets/halueval_dialogue_summ_general_n300.json": "read_required",
    "artifacts/beaaa-18720/unified-probe-inventory.jsonl": "read_required",
    "artifacts/beaaa-35661/construction_dispositions.json": "read_required",
    "artifacts/beaaa-19322/vapi-2000-scoreability-ledger-v1.1.json": "read_required",
    "artifacts/beaaa-19929/canonical-probe-vapi-backlog-v1.0.jsonl": "read_required",
    "artifacts/beaaa-21704/execution_evidence.json": "read_required",
    "artifacts/beaaa-31092/rank2-ledger.json": "read_required",
    "artifacts/beaaa-35380/tool-misuse-dev-activation-ledger.json": "read_required",
    # BEAAA-35665: read_optional, not read_required. The loader returns {} when this
    # ledger is absent, because deleting it IS the documented per-slice rollback
    # (BEAAA-35691) -- so absence is the designed state, not a defect the image must
    # prevent. The classification rests on that reason alone. The ledger IS present
    # as of BEAAA-35915; an earlier revision of this comment claimed it was absent
    # because the first confidence_calibration capture had been denied as an
    # activation basis, and the BEAAA-35753 refit falsified that.
    "artifacts/beaaa-35665/cc-dev-activation-ledger.json": "read_optional",
    "artifacts/framework-workers": "output",
    "artifacts/full-scan-evidence": "output",
    # Framework-worker jobs write per-run evidence here; they never read a
    # pre-existing repository artifact from this path.
    "artifacts/framework-workers": "output",
    "artifacts/beaaa-18720/reconciliation-validation.json": "reference_only",
    "artifacts/beaaa-35020/tool_misuse_corrected_n5_final.json": "reference_only",
    # Carried as the execution_evidence_ref string on a promoted row, never opened --
    # same shape as the beaaa-35020 reference above. cc_slice_capture is the
    # easy+medium capture the first pass wrote; cc_refit_slice is the BEAAA-35753
    # refit's aggregate measurement and is the ref the dev-scored rows actually
    # carry today. Both are strings in the row payload, neither is opened.
    "artifacts/beaaa-35665/cc_slice_capture.json": "reference_only",
    "artifacts/beaaa-35753/cc_refit_slice.json": "reference_only",
    # BEAAA-35938: same classification and same reason as the beaaa-35665 ledger
    # above -- read_optional because deleting this file IS the documented per-slice
    # rollback (measured: the dev-mode scoring_active count returns from 925 to the
    # master 819 with the file removed, and the build still exits 0). The capture is
    # reference_only: it is carried as the execution_evidence_ref string on each
    # promoted row and is never opened.
    "artifacts/beaaa-35938/hl-dev-activation-ledger.json": "read_optional",
    "artifacts/beaaa-35938/hl_slice_capture.json": "reference_only",
}

READ_CLASSIFICATIONS = frozenset({"read_required", "read_optional"})
VALID_CLASSIFICATIONS = READ_CLASSIFICATIONS | {"output", "reference_only"}


@dataclass(frozen=True)
class AllowlistFinding:
    """One ``read_required`` path the image would not carry."""

    path: str
    classification: str

    def __str__(self) -> str:  # pragma: no cover - formatting only
        return (
            f"{self.path} is {self.classification} at runtime but .dockerignore "
            f"excludes it from the image"
        )


def _dockerignore_patterns(text: str) -> list[tuple[str, bool]]:
    """``(pattern, negated)`` in file order, comments and blanks dropped."""
    patterns: list[tuple[str, bool]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:].strip()
        patterns.append((line.rstrip("/"), negated))
    return patterns


def _path_prefixes(rel_path: str) -> Iterator[str]:
    parts = PurePosixParts(rel_path)
    for index in range(1, len(parts) + 1):
        yield "/".join(parts[:index])


def PurePosixParts(rel_path: str) -> list[str]:
    return [part for part in rel_path.strip("/").split("/") if part]


def image_includes(rel_path: str, patterns: Iterable[tuple[str, bool]] | None = None) -> bool:
    """Would ``COPY . .`` carry ``rel_path`` into the image?

    Docker evaluates patterns in file order with last-match-wins, and a path
    inside an excluded directory is only restored when a later negation
    re-includes the directory itself. So walk the prefixes: the path ships only
    if no ancestor, and not the path, ends up excluded.
    """
    if patterns is None:
        patterns = _dockerignore_patterns(DOCKERIGNORE_PATH.read_text(encoding="utf-8"))
    pattern_list = list(patterns)
    for prefix in _path_prefixes(rel_path):
        excluded = False
        for pattern, negated in pattern_list:
            if _matches(pattern, prefix):
                excluded = not negated
        if excluded:
            return False
    return True


def _matches(pattern: str, prefix: str) -> bool:
    """``fnmatch`` per path segment — a single ``*`` never crosses a ``/``."""
    pattern_parts = PurePosixParts(pattern)
    prefix_parts = PurePosixParts(prefix)
    if len(pattern_parts) != len(prefix_parts):
        return False
    return all(
        fnmatchcase(prefix_part, pattern_part)
        for pattern_part, prefix_part in zip(pattern_parts, prefix_parts)
    )


def live_module_names() -> tuple[str, ...]:
    """``LIVE_MODULES`` from the build's own import-closure guard."""
    spec = importlib.util.spec_from_file_location(
        "_beaaa35637_runtime_import_closure",
        REPO_ROOT / "scripts" / "verify_runtime_import_closure.py",
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("cannot load scripts/verify_runtime_import_closure.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(module.LIVE_MODULES)


def _first_party_source(module_name: str) -> Path | None:
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, AttributeError, ValueError):
        return None
    if spec is None or not spec.origin or spec.origin in {"built-in", "frozen"}:
        return None
    path = Path(spec.origin)
    try:
        path.relative_to(REPO_ROOT)
    except ValueError:
        return None
    return path if path.suffix == ".py" else None


def runtime_module_files(seeds: Iterable[str] | None = None) -> dict[str, Path]:
    """Transitive first-party import closure of the live runtime modules."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    pending = list(seeds if seeds is not None else live_module_names())
    seen: set[str] = set()
    files: dict[str, Path] = {}
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        source = _first_party_source(name)
        if source is None:
            continue
        files[name] = source
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):  # pragma: no cover - defensive
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                pending.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                pending.append(node.module)
                pending.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return files


def _docstring_nodes(tree: ast.AST) -> set[int]:
    holders = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, holders) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
    return ids


def _binop_segments(node: ast.AST) -> list[str] | None:
    """Flatten ``ROOT / "artifacts" / "beaaa-x" / "y.json"`` to its constants."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _binop_segments(node.left)
        right = _binop_segments(node.right)
        if right is None:
            return None
        return (left or []) + right
    return None


def _candidate_paths(source: Path) -> set[str]:
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):  # pragma: no cover - defensive
        return set()
    docstrings = _docstring_nodes(tree)
    # A ``a / b / c`` chain contains ``a / b`` as a sub-node. Only the outermost
    # chain is a real path; the prefixes are parse artefacts.
    nested: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            for child in (node.left, node.right):
                if isinstance(child, ast.BinOp) and isinstance(child.op, ast.Div):
                    nested.add(id(child))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            if id(node) in nested:
                continue
            segments = _binop_segments(node)
            if segments and "artifacts" in segments:
                start = segments.index("artifacts")
                found.add("/".join(segments[start:]))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            value = node.value.strip()
            if value.startswith("artifacts/"):
                found.add(value)
    # Templates and globs are not concrete paths; the concrete prefix is what
    # matters and it is reached through the directory entry in the inventory.
    # Evidence references pin a path with a ``#anchor`` or ``@ref``; the path is
    # the part before the pin.
    concrete = {re.split(r"[#@]", path, maxsplit=1)[0].rstrip("/") for path in found}
    return {
        path
        for path in concrete
        if "{" not in path and "*" not in path and "<" not in path and len(PurePosixParts(path)) > 1
    }


def scan_runtime_artifact_literals() -> dict[str, set[str]]:
    """``artifacts/`` path -> modules naming it, across the runtime closure."""
    by_path: dict[str, set[str]] = {}
    for module_name, source in runtime_module_files().items():
        for path in _candidate_paths(source):
            by_path.setdefault(path, set()).add(module_name)
    return by_path


#: Keys in a C10 activation manifest whose value is itself a repo-relative
#: ``artifacts/`` path the runtime opens. A source scan cannot see these:
#: the path is data, not a literal.
MANIFEST_DECLARED_PATH_KEYS = ("canonical_backlog_path", "scoreability_ledger_path")


def scan_manifest_declared_artifacts() -> dict[str, set[str]]:
    """``artifacts/`` path -> manifests declaring it, for data-named reads."""
    by_path: dict[str, set[str]] = {}
    activation_dir = REPO_ROOT / "artifacts" / "BEAAA-26529"
    if not activation_dir.is_dir():
        return by_path
    for manifest_path in sorted(activation_dir.rglob("*.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        evidence = manifest.get("evidence")
        if not isinstance(evidence, dict):
            continue
        for key in MANIFEST_DECLARED_PATH_KEYS:
            declared = evidence.get(key)
            if isinstance(declared, str) and declared.startswith("artifacts/"):
                relative = str(manifest_path.relative_to(REPO_ROOT))
                by_path.setdefault(declared, set()).add(f"{relative}:{key}")
    return by_path


def audit() -> list[AllowlistFinding]:
    """``read_required`` inventory entries the image would not carry."""
    patterns = _dockerignore_patterns(DOCKERIGNORE_PATH.read_text(encoding="utf-8"))
    return [
        AllowlistFinding(path=path, classification=classification)
        for path, classification in sorted(RUNTIME_ARTIFACT_INVENTORY.items())
        if classification == "read_required" and not image_includes(path, patterns)
    ]


def main() -> int:  # pragma: no cover - CLI convenience
    findings = audit()
    for finding in findings:
        print(str(finding), file=sys.stderr)
    named = set(scan_runtime_artifact_literals()) | set(scan_manifest_declared_artifacts())
    undeclared = sorted(named - set(RUNTIME_ARTIFACT_INVENTORY))
    for path in undeclared:
        print(f"{path} is read at runtime but not declared in RUNTIME_ARTIFACT_INVENTORY", file=sys.stderr)
    if findings or undeclared:
        return 1
    print(f"image artifact allowlist clean: {len(RUNTIME_ARTIFACT_INVENTORY)} declared paths")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI convenience
    raise SystemExit(main())
