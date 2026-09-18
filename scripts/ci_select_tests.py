#!/usr/bin/env python3
"""Pick the CI work a diff can actually affect.

Given the list of files a push or pull request changed, this script decides

* which Python test files under ``tests/`` can be affected (``tests``), and
* which CI jobs need to run at all (one boolean "gate" per job family).

It is deliberately fail-closed: whenever it cannot tell, it answers "run
everything" (``selection_mode=ALL``). A wrong answer therefore costs runner
minutes, never a missed regression. The nightly scheduled run of
``tests.yml`` always runs everything, so anything the selector misses on a
PR is still caught within a day.

How affected tests are found
----------------------------
1. Every ``*.py`` file in the repository is parsed once (``ast``) and its
   repository-internal imports become edges of a module graph.
2. A changed ``.py`` file affects every test file whose transitive import
   closure contains it. Changed test files are always selected.
3. A changed non-Python file (fixture, corpus row, YAML mapping, canon
   document...) affects every ``.py`` file whose source text mentions its
   path, its basename, or one of its parent directories; those files are
   then treated like changed modules in step 2.
4. Some files invalidate all knowledge (``tests/conftest.py``, this script,
   the workflow that calls it, the dependency locks): any change there means
   ALL. If more than ``ALL_THRESHOLD`` of the test files are selected, ALL is
   cheaper and safer, so ALL is returned. A changed core module that no test
   imports also means ALL (``CORE_PACKAGES``); a changed helper under
   ``scripts/``/``tools/``/``ops/`` that no test references selects nothing.

Usage
-----
    ci_select_tests.py --changed-json '["api/app.py", "docs/x.md"]' \
        [--github-output "$GITHUB_OUTPUT"] [--summary "$GITHUB_STEP_SUMMARY"]
    ci_select_tests.py --changed-file changed.txt
    ci_select_tests.py --full            # nightly / manual: everything
    ci_select_tests.py --expand          # inside the pytest job: prints the
                                         # pytest targets, one per line, from
                                         # $SELECTED_TESTS / $SELECTION_MODE

Outputs written to ``--github-output`` (all strings):
    selection_mode  ALL | SOME | NONE
    tests           JSON list of pytest targets (["tests"] for ALL, [] for NONE)
    code            "true" when the pytest / CLI-render jobs must run
    <gate>          "true"/"false" for every name in GATES
    reason          one line explaining the decision
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from collections import deque
from pathlib import Path, PurePosixPath

# ---------------------------------------------------------------------------
# Policy tables. Edit these, not the algorithm, when CI shape changes.
# ---------------------------------------------------------------------------

# Any change to one of these means the selector cannot reason: run everything.
INVALIDATE_ALL = (
    ".github/workflows/tests.yml",
    ".github/workflows/pr-unit-tests.yml",
    "scripts/ci_select_tests.py",
    "tests/conftest.py",
    "tests/__init__.py",
)
INVALIDATE_ALL_PREFIXES = (
    "locks/",
    "requirements",  # requirements*.txt and requirements/
)

# A changed module here with no importing test is suspicious -> ALL.
CORE_PACKAGES = (
    "api",
    "rubric",
    "scanners",
    "harness",
    "report_card",
    "probes",
    "corpus",
    "config",
    "bin",
    "framework_assessment.py",
    "release_identity.py",
)

# Directories never walked for the import graph.
SKIP_DIRS = {
    ".git",
    ".cache",
    ".tmp",
    ".gstack",
    "__pycache__",
    "node_modules",
    "third_party",
    ".venv",
    "venv",
}

# If the selection covers more than this share of test files, run everything.
ALL_THRESHOLD = 0.6

# Job gates. A gate is "true" when selection_mode is ALL, or when any changed
# file starts with one of its prefixes, or when any selected test is in its
# tests list. Prefixes are matched against the repo-relative POSIX path.
GATES: dict[str, dict[str, tuple[str, ...]]] = {
    # ruff / mypy / vulture / coverage on rubric/scoring.py (tests.yml)
    "scoring": {
        "prefixes": ("rubric/",),
        "tests": (
            "tests/test_scoring.py",
            "tests/test_beaaa198_phase3_scoring.py",
            "tests/test_golden_agents.py",
        ),
    },
    # eval-framework-venvs matrix (tests.yml)
    "evalvenv": {
        "prefixes": (
            "locks/",
            "requirements",
            "scripts/build_eval_framework_venvs.sh",
            "scanners/eval_framework_venv.py",
            "tests/scanners/",
            "rubric/probe_mappings/",
            "tools/validate_garak_registry.py",
        ),
        "tests": (
            "tests/test_inspect_runner.py",
            "tests/unit/test_inspect_provenance_pin.py",
            "tests/test_native_worker_venvs_beaaa16616.py",
            "tests/test_garak_registry_resolution_beaaa31623.py",
            "tests/scanners/test_eval_framework_venv.py",
        ),
    },
    # AIUC-1 direct families production binding (folded into tests.yml)
    "aiuc1": {
        "prefixes": (
            "api/",
            "harness/",
            "tools/",
            "probes/",
            "corpus/",
            "generated/",
            "rubric/",
            "scripts/verify_aiuc1",
            "locks/",
            "requirements",
        ),
        "tests": (
            "tests/test_authentication_bypass_resistance_beaaa26755.py",
            "tests/test_beaaa26756_sensitive_data_disclosure.py",
            "tests/test_day1_report_only_beaaa26757.py",
            "tests/test_aiuc1_direct_families_production_wiring.py",
        ),
    },
    # authoritative-probe-registry drift gate (folded into tests.yml)
    "registry": {
        "prefixes": (
            "api/",
            "harness/",
            "tools/",
            "probes/",
            "corpus/",
            "generated/",
            "rubric/",
            "scanners/",
            "scripts/authoritative_probe_registry",
            "requirements-scanner-regression.txt",
        ),
        "tests": ("tests/test_authoritative_probe_registry_beaaa20357.py",),
    },
    # golden scanner regression + N=5 gates (folded into tests.yml)
    "regression": {
        "prefixes": (
            "api/",
            "rubric/",
            "scanners/",
            "harness/",
            "report_card/",
            "corpus/",
            "probes/",
            "fixtures/",
            "config/",
            "generated/",
            "bin/",
            "scripts/beaaa6190",
            "tools/full_scan_reference_agents",
            "tools/calibration-deltas/",
            "requirements-scanner-regression.txt",
        ),
        "tests": (
            "tests/test_golden_agents.py",
            "tests/test_beaaa23968_corpus_pin.py",
            "tests/test_beaaa24002_corpus_pin_p13_p15.py",
            "tests/test_cli_runner_beaaa464.py",
            "tests/test_p1_1_factuality_golden.py",
            "tests/test_p1_3_consistency_golden.py",
            "tests/test_p1_4_refusal_golden.py",
            "tests/test_p1_5_calibration_golden.py",
            "tests/test_scanner_tamper_red_team.py",
            "tests/test_release_gate_beaaa10189.py",
            "tests/test_full_scan_reference_agents_beaaa13803.py",
        ),
    },
    # pr-unit-tests.yml jobs
    "brain": {
        "prefixes": ("requirements-dev.txt",),
        "tests": (
            "tests/test_r2_bias_families.py",
            "tests/test_r2_rob_families.py",
        ),
    },
    "portal": {
        "prefixes": (
            "tools/portal_v1/",
            "tools/staging-endpoint-fixture/",
            "tools/connector-static-attest/",
            "requirements-dev.txt",
        ),
        "tests": (),
    },
    "probe_sdk": {
        "prefixes": ("engineering/probe-sdk/", "requirements-dev.txt"),
        "tests": (),
    },
    "connector": {
        "prefixes": ("tools/are-connector/", "requirements-dev.txt"),
        "tests": (),
    },
    "go": {"prefixes": ("tools/are-collector/",), "tests": ()},
    "runtime_slice": {
        "prefixes": (
            "requirements-runtime.txt",
            "api/",
            "rubric/",
            "scanners/",
            "harness/",
            "report_card/",
            "probes/",
            "tools/",
            "scripts/",
        ),
        "tests": ("tests/test_runtime_import_closure_beaaa13329.py",),
    },
}

TEST_FILE_RE = re.compile(r"^(test_.*|.*_test)\.py$")


# ---------------------------------------------------------------------------
# Repository model
# ---------------------------------------------------------------------------


class Repo:
    """All ``.py`` files, their text, and the internal import graph."""

    def __init__(self, root: Path):
        self.root = root
        self.files: dict[str, str] = {}  # relpath -> source text
        self.module_to_file: dict[str, str] = {}
        self.imports: dict[str, set[str]] = {}  # relpath -> relpaths
        self.reverse: dict[str, set[str]] = {}
        self._load()
        self._build_graph()

    # -- loading -----------------------------------------------------------
    def _load(self) -> None:
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(
                d for d in dirnames if d not in SKIP_DIRS and not d.endswith("-venv")
            )
            for name in filenames:
                if not name.endswith(".py"):
                    continue
                path = Path(dirpath) / name
                rel = path.relative_to(self.root).as_posix()
                try:
                    text = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                self.files[rel] = text
                for mod in _module_names(rel):
                    self.module_to_file.setdefault(mod, rel)

    def _build_graph(self) -> None:
        for rel, text in self.files.items():
            targets: set[str] = set()
            try:
                tree = ast.parse(text, filename=rel)
            except SyntaxError:
                # Unparseable file: cannot know its imports. Treated as a
                # leaf; a change to it still selects tests that name it.
                self.imports[rel] = targets
                continue
            package = _package_of(rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        targets.update(self._resolve(alias.name))
                elif isinstance(node, ast.ImportFrom):
                    base = node.module or ""
                    if node.level:
                        parts = package.split(".") if package else []
                        parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                        base = ".".join(p for p in parts + ([base] if base else []) if p)
                    if base:
                        targets.update(self._resolve(base))
                    for alias in node.names:
                        if base and alias.name != "*":
                            targets.update(self._resolve(f"{base}.{alias.name}"))
            targets.discard(rel)
            self.imports[rel] = targets
        for src, dsts in self.imports.items():
            for dst in dsts:
                self.reverse.setdefault(dst, set()).add(src)

    def _resolve(self, dotted: str) -> set[str]:
        """Longest known prefix of ``dotted`` -> file (if repo-internal)."""
        parts = dotted.split(".")
        while parts:
            hit = self.module_to_file.get(".".join(parts))
            if hit:
                return {hit}
            parts.pop()
        return set()

    # -- queries -----------------------------------------------------------
    def test_files(self) -> list[str]:
        return sorted(
            rel
            for rel in self.files
            if rel.startswith("tests/") and TEST_FILE_RE.match(PurePosixPath(rel).name)
        )

    def dependents(self, changed: set[str]) -> set[str]:
        """Every file whose import closure contains any changed file."""
        seen: set[str] = set(changed)
        queue = deque(changed)
        while queue:
            cur = queue.popleft()
            for parent in self.reverse.get(cur, ()):
                if parent not in seen:
                    seen.add(parent)
                    queue.append(parent)
        return seen

    def files_mentioning(self, tokens: list[re.Pattern[str]]) -> set[str]:
        """Files whose text matches any token (one combined scan per chunk)."""
        hits: set[str] = set()
        chunk = 150  # keep each alternation a sane size
        for i in range(0, len(tokens), chunk):
            combined = re.compile("|".join(f"(?:{p.pattern})" for p in tokens[i : i + chunk]))
            for rel, text in self.files.items():
                if rel not in hits and combined.search(text):
                    hits.add(rel)
        return hits


def _module_names(rel: str) -> list[str]:
    p = PurePosixPath(rel)
    parts = list(p.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return []
    return [".".join(parts)]


def _package_of(rel: str) -> str:
    """Dotted package a module lives in (its directory), for relative imports."""
    return ".".join(PurePosixPath(rel).parts[:-1])


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _reference_patterns(rel: str) -> list[re.Pattern[str]]:
    """Regexes that indicate a .py file refers to data file ``rel``."""
    p = PurePosixPath(rel)
    pats = [re.compile(re.escape(rel))]
    if len(p.name) >= 5:
        pats.append(re.compile(r"(?<![\w./-])" + re.escape(p.name) + r"(?![\w-])"))
    # Parent directories at depth >= 2 ("tests/fixtures/", not "tests/"),
    # so a directory-level glob in a test still counts as a reference.
    parents = list(p.parents)[:-1]  # drop "."
    for parent in parents:
        if len(parent.parts) >= 2:
            pats.append(re.compile(r"(?<![\w./-])" + re.escape(parent.as_posix() + "/")))
    return pats


def _module_patterns(rel: str) -> list[re.Pattern[str]]:
    """Regexes for a (possibly deleted) module referenced by path or name."""
    pats = [re.compile(re.escape(rel))]
    for mod in _module_names(rel):
        pats.append(re.compile(r"(?<![\w.])" + re.escape(mod) + r"(?![\w])"))
    return pats


def _is_core(rel: str) -> bool:
    top = rel.split("/", 1)[0]
    return top in CORE_PACKAGES


def select(repo: Repo, changed: list[str], full: bool = False) -> dict[str, object]:
    changed = sorted({c.strip().replace("\\", "/") for c in changed if c and c.strip()})
    all_tests = repo.test_files()
    reasons: list[str] = []

    def result(mode: str, tests: list[str], why: str) -> dict[str, object]:
        reasons.append(why)
        gates = _gates(mode, changed, tests)
        return {
            "selection_mode": mode,
            "tests": ["tests"] if mode == "ALL" else tests,
            "code": mode != "NONE",
            "gates": gates,
            "reason": "; ".join(reasons),
            "changed": changed,
        }

    if full:
        return result("ALL", [], "full run requested")
    if not changed:
        return result("ALL", [], "no changed-file list available (fail closed)")

    for rel in changed:
        if rel in INVALIDATE_ALL or rel.startswith(INVALIDATE_ALL_PREFIXES):
            return result("ALL", [], f"{rel} invalidates test selection")

    seeds: set[str] = set()
    selected: set[str] = set()
    text_patterns: list[re.Pattern[str]] = []
    text_sources = 0
    for rel in changed:
        if rel.endswith(".py"):
            if rel in repo.files:
                seeds.add(rel)
                if rel.startswith("tests/") and TEST_FILE_RE.match(PurePosixPath(rel).name):
                    selected.add(rel)
            else:
                # Deleted or renamed away: whoever still names it is affected.
                text_patterns.extend(_module_patterns(rel))
                text_sources += 1
        else:
            text_patterns.extend(_reference_patterns(rel))
            text_sources += 1
    if text_patterns:
        hits = repo.files_mentioning(text_patterns)
        seeds.update(hits)
        reasons.append(f"{text_sources} non-module files, {len(hits)} referring .py files")

    affected = repo.dependents(seeds) if seeds else set()
    for rel in affected:
        if rel in all_tests:
            selected.add(rel)

    # A changed core module nobody tests is a gap we refuse to paper over.
    for rel in changed:
        if rel.endswith(".py") and rel in repo.files and _is_core(rel):
            if not (repo.dependents({rel}) & set(all_tests)):
                return result("ALL", [], f"core module {rel} has no importing test")

    if not selected:
        return result("NONE", [], f"{len(changed)} changed files touch no test")
    if len(selected) > ALL_THRESHOLD * max(len(all_tests), 1):
        return result(
            "ALL", [], f"{len(selected)}/{len(all_tests)} tests selected; whole tree is cheaper"
        )
    tests = sorted(selected)
    return result("SOME", tests, f"{len(tests)}/{len(all_tests)} tests affected")


def _gates(mode: str, changed: list[str], tests: list[str]) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for name, rule in GATES.items():
        if mode == "ALL":
            out[name] = True
            continue
        by_path = any(c.startswith(rule["prefixes"]) for c in changed) if rule["prefixes"] else False
        by_test = bool(set(tests) & set(rule["tests"]))
        out[name] = by_path or by_test
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


# Tests that only pass inside an eval-framework venv (they import inspect_ai,
# which the plain pytest job does not install). `tests.yml` already passes
# `--ignore` for both, but pytest applies `--ignore` only while *collecting a
# directory* — a file named explicitly on the command line is always collected.
# The selector emits explicit file targets, so the ignore was silently bypassed
# whenever a diff pulled these two into SELECTED_TESTS, and the job failed with
# "inspect-ai not installed". They are run for real by the `eval venv (inspect)`
# matrix job (see GATES["evalvenv"]), so dropping them here loses no coverage.
EVAL_VENV_ONLY_TESTS: frozenset[str] = frozenset(
    {
        "tests/test_inspect_runner.py",
        "tests/unit/test_inspect_provenance_pin.py",
    }
)


def _expand() -> list[str]:
    mode = os.environ.get("SELECTION_MODE", "").strip()
    raw = os.environ.get("SELECTED_TESTS", "").strip()
    if mode == "NONE":
        return []
    if mode != "SOME" or not raw:
        return ["tests"]  # ALL, or the changes job never reported: whole tree
    try:
        targets = json.loads(raw)
    except json.JSONDecodeError:
        return ["tests"]
    targets = [t for t in targets if isinstance(t, str) and t]
    targets = [t for t in targets if t not in EVAL_VENV_ONLY_TESTS]
    return targets or ["tests"]


def _write_outputs(res: dict[str, object], path: str | None, summary: str | None) -> None:
    lines = [
        f"selection_mode={res['selection_mode']}",
        f"tests={json.dumps(res['tests'])}",
        f"code={'true' if res['code'] else 'false'}",
        f"reason={res['reason']}",
    ]
    for name, val in res["gates"].items():  # type: ignore[union-attr]
        lines.append(f"{name}={'true' if val else 'false'}")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("### CI selection\n\n")
            fh.write(f"- mode: **{res['selection_mode']}** — {res['reason']}\n")
            fh.write(f"- changed files: {len(res['changed'])}\n")  # type: ignore[arg-type]
            gates_on = [k for k, v in res["gates"].items() if v]  # type: ignore[union-attr]
            fh.write(f"- jobs on: {', '.join(gates_on) or 'none'}\n")
            if res["selection_mode"] == "SOME":
                fh.write("\n<details><summary>selected tests</summary>\n\n")
                for t in res["tests"]:  # type: ignore[union-attr]
                    fh.write(f"- `{t}`\n")
                fh.write("\n</details>\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".", help="repository root")
    ap.add_argument("--changed-json", help="JSON array of changed paths ('-' = stdin)")
    ap.add_argument("--changed-file", help="file with one changed path per line")
    ap.add_argument("--full", action="store_true", help="select everything")
    ap.add_argument("--github-output", help="append outputs here ($GITHUB_OUTPUT)")
    ap.add_argument("--summary", help="append a markdown summary here ($GITHUB_STEP_SUMMARY)")
    ap.add_argument("--expand", action="store_true", help="print pytest targets from env and exit")
    ap.add_argument("--json", action="store_true", help="print the full decision as JSON")
    args = ap.parse_args(argv)

    if args.expand:
        for t in _expand():
            print(t)
        return 0

    changed: list[str] = []
    if args.changed_json:
        raw = sys.stdin.read() if args.changed_json == "-" else args.changed_json
        try:
            changed = json.loads(raw) if raw.strip() else []
        except json.JSONDecodeError:
            print("::warning::changed-file list is not JSON; running everything", file=sys.stderr)
            changed = []
        if not isinstance(changed, list):
            changed = []
    elif args.changed_file:
        changed = Path(args.changed_file).read_text(encoding="utf-8").splitlines()

    repo = Repo(Path(args.root).resolve())
    res = select(repo, [str(c) for c in changed], full=args.full)
    _write_outputs(res, args.github_output, args.summary)
    print(f"ci_select_tests: {res['selection_mode']} — {res['reason']}", file=sys.stderr)
    if args.json:
        print(json.dumps(res, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
