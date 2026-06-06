#!/usr/bin/env python3
"""BEAAA-2182 Track A — AG-A5 CI gate.

Static check: no module under ``api/`` may import the scoring callee
(``rubric.scoring``) OR the deleted API-edge scoring runtime module
(``api.score_runtime``) at module-import-statement granularity.

Rationale (per BEAAA-2182 ADR-0026 §AG-A5):

Track A moves the scoring callee into the Scorer subprocess (deny-all
egress scope per Hard Invariant #5). The API-edge factory wires the
Scorer through ``scanners.score_runtime`` (which itself imports
``rubric.scoring``), but the import surface on ``api/`` modules must
stay clean so:

* No route handler ever transitively calls ``rubric.scoring.score`` on
  the API edge (tamper-resistance posture).
* The deleted ``api.score_runtime`` cannot be silently re-introduced.

Q-A3 ACCEPT on BEAAA-2182 wake comment ``9840343c``: CI check is the
durable gate; a Scorer-startup runtime assertion (under ``ARE_TEST_MODE``)
catches drift in the test session.

Allowed imports under ``api/``:

* ``scanners.score_runtime`` (the Scorer-side runtime; parent loads it
  to fork-inherit into the Scorer per Q-A1 ACCEPT). The import lives
  inside ``make_default_production_scan_runtime`` as a function-local
  import so module-level imports stay clean.

Usage:

    python3 scripts/check_no_apiedge_score.py [--repo-root .]

Exits 0 on pass, 1 on any match. Run in CI on every PR; bypassable
neither by ``--no-verify`` (pre-commit) nor by env override.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Forbidden import patterns at the module-import-statement level. Each
# regex matches a single line of source. The patterns are deliberately
# strict (line-anchored at start, comment-friendly via end-of-line) so
# string mentions inside docstrings or comments do not trip the gate.
#
# Scope (Track A): block re-introduction of the DELETED ``api.score_runtime``
# module (the BEAAA-2175 PR-B Phase 2 scoring runtime that ran on the API
# edge). The Scorer-side runtime lives at ``scanners.score_runtime`` and
# is fork-inherited into the Scorer subprocess for execution under the
# deny-all egress scope (Hard Invariant #5).
#
# Not scoped here: a separate informational warning lists API-edge
# ``rubric.scoring`` imports (legacy ``/score`` and ``/forensic_bundle``
# route handlers in ``api/handlers.py`` / ``api/app.py``). Those routes
# pre-date Track A and consume caller-supplied ``eval_outputs`` dicts
# (offline eval scoring; the customer-agent surface that Track A
# protects is ``/scanner/v1/rescans``). Tightening the legacy route is a
# separate ticket — call out as informational only here, not a CI fail.
FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "api.score_runtime is deleted (BEAAA-2182 Track A); the Scorer-side "
        "scoring runtime lives at scanners.score_runtime and is loaded "
        "function-locally in api.scan_runtime_factory."
        "make_default_production_scan_runtime so the Scorer subprocess "
        "inherits it via fork (Q-A1 ACCEPT on wake comment 9840343c).",
        re.compile(r"^\s*(?:from\s+api\.score_runtime\s+import|import\s+api\.score_runtime(?:\s|$))"),
    ),
)

# Informational only — surfaces pre-existing API-edge scoring imports
# so future tightening can target them, but does NOT fail CI today.
# Track A scope is the new /scanner/v1/rescans surface, not the legacy
# /score handler. Removing this informational layer requires a follow-up
# ticket that refactors api/handlers.py::score_handler.
INFORMATIONAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "rubric.scoring imported by API edge (legacy /score / "
        "/forensic_bundle handler — pre-existing; not blocked by Track A)",
        re.compile(r"^\s*(?:from\s+rubric\.scoring\s+import|import\s+rubric\.scoring(?:\s|$))"),
    ),
)


def scan_path(
    api_dir: Path,
) -> tuple[list[tuple[Path, int, str, str]], list[tuple[Path, int, str, str]]]:
    """Return (forbidden_hits, informational_hits) under ``api_dir``.

    Forbidden hits fail CI; informational hits are logged but do not
    fail the gate (pre-existing tech debt outside Track A scope).
    """
    forbidden: list[tuple[Path, int, str, str]] = []
    informational: list[tuple[Path, int, str, str]] = []
    for py_path in sorted(api_dir.rglob("*.py")):
        try:
            text = py_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(
                f"[check_no_apiedge_score] WARN: cannot read {py_path}: {exc}",
                file=sys.stderr,
            )
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if "#" in line:
                line_for_match = line.split("#", 1)[0]
            else:
                line_for_match = line
            for reason, pattern in FORBIDDEN_PATTERNS:
                if pattern.search(line_for_match):
                    forbidden.append((py_path, line_no, line.rstrip(), reason))
                    break
            for reason, pattern in INFORMATIONAL_PATTERNS:
                if pattern.search(line_for_match):
                    informational.append((py_path, line_no, line.rstrip(), reason))
                    break
    return forbidden, informational


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Project root containing api/ (default: repo root)",
    )
    args = parser.parse_args()

    api_dir = (args.repo_root / "api").resolve()
    if not api_dir.is_dir():
        print(
            f"[check_no_apiedge_score] ERROR: api/ not found at {api_dir}",
            file=sys.stderr,
        )
        return 2

    forbidden, informational = scan_path(api_dir)

    # Informational layer — pre-existing /score handler imports. Logged
    # but never fail CI in Track A.
    if informational:
        print(
            f"[check_no_apiedge_score] INFO: {len(informational)} "
            f"pre-existing API-edge rubric.scoring import(s) (not blocked "
            f"by Track A; future-refactor candidates):"
        )
        for path, line_no, line, reason in informational:
            rel = path.relative_to(args.repo_root)
            print(f"  {rel}:{line_no}: {line.strip()}")
            print(f"    reason: {reason}")

    if forbidden:
        print(
            f"\n[check_no_apiedge_score] AG-A5 FAIL: {len(forbidden)} "
            f"forbidden import(s) found under {api_dir}/",
            file=sys.stderr,
        )
        for path, line_no, line, reason in forbidden:
            rel = path.relative_to(args.repo_root)
            print(
                f"  {rel}:{line_no}: {line}",
                file=sys.stderr,
            )
            print(f"    reason: {reason}", file=sys.stderr)
        print(
            "\nAG-A5 (BEAAA-2182 ADR-0026): the deleted api.score_runtime "
            "module cannot be re-introduced on the API edge. The Scorer-"
            "side scoring runtime lives at scanners.score_runtime and is "
            "loaded function-locally in api.scan_runtime_factory."
            "make_default_production_scan_runtime so the Scorer subprocess "
            "inherits it via fork. If a new API-edge code path genuinely "
            "needs to compose with the Scorer-side scoring runtime, raise "
            "a CTO co-sign request before making the change.",
            file=sys.stderr,
        )
        return 1

    print(
        f"\n[check_no_apiedge_score] AG-A5 PASS — 0 forbidden "
        f"api.score_runtime imports under {api_dir}/"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
