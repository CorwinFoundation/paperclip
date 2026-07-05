#!/usr/bin/env python3
"""OSS clean-export ref-scrubber.

Strips internal references (issue-tracker ids, internal design-doc ids,
internal e-mail domains) and legacy product naming from the comments and
docstrings of the public shipping set, WITHOUT touching any runtime
identifier, function name, env-var name, or the grandfathered public
contract / action names (``are-scanner-api-v1``, ``backbond/are-scanner-
action``, ``ARE_SCAN_*`` env vars, ``are_total`` fields).

Two entry points:

* ``scrub <dir>``  — apply the rewrite rules in place across the export tree.
* ``gate  <dir>``  — scan the tree and exit non-zero on ANY forbidden token.

The gate is authoritative: ``scrub`` runs it at the end and refuses to leave a
dirty tree. The publish pipeline (``clean_export.sh``) also runs ``gate`` as an
independent second pass before it builds the squashed bundle, so a bad export
can never reach a push.

This tool is internal build tooling. It is NOT part of the shipping set and is
never itself exported or published.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Gate: patterns that MUST NOT appear anywhere in the exported shipping set.
#
# Case-insensitive for the id families so path-style spellings (``beaaa11714``,
# ``adr-0042``) are caught alongside the canonical ``BEAAA-1234`` / ``ADR-0042``.
# The ARE product-name check is UPPERCASE-WORD only: it flags the legacy
# ``ARE Scanner`` naming but deliberately leaves lowercase interface identifiers
# (``are-scanner-api-v1``, ``are_total``) and ``ARE_SCAN_*`` env vars intact.
# ---------------------------------------------------------------------------
FORBIDDEN: list[tuple[str, re.Pattern[str]]] = [
    ("issue-tracker id", re.compile(r"beaaa[-_ ]?\d+", re.IGNORECASE)),
    ("internal ADR id", re.compile(r"\badr[-_ ]?\d+", re.IGNORECASE)),
    ("internal domain: beai.ing", re.compile(r"beai\.ing")),
    ("internal domain: are-scanner.internal", re.compile(r"are-scanner\.internal")),
    ("internal domain: corwinfoundation.local", re.compile(r"corwinfoundation\.local")),
    ("internal domain: ifa.invalid", re.compile(r"ifa\.invalid")),
    ("legacy product name: ARE", re.compile(r"\bARE\b")),
]

# ---------------------------------------------------------------------------
# Rewrite rules, keyed by path relative to the export root. Each rule is
# (pattern, replacement, is_regex). Regex rules use DOTALL so multi-line
# docstring blocks can be rewritten wholesale. Every configured `find` MUST
# match at least once (checked below) so a source drift that silences a rule
# is a hard error, not a silent leak.
# ---------------------------------------------------------------------------
Rule = tuple[str, str, bool]

REWRITES: dict[str, list[Rule]] = {
    "api/__init__.py": [
        (
            '"""ARE Scanner v0.1 API surface (BEAAA-21 v0.2 contract)."""',
            '"""BackBond Scanner API surface."""',
            False,
        ),
    ],
    "api/jcs.py": [
        (
            "BEAAA-1271 / ADR-0009 §4.2. Used to canonicalize the ",
            "Used to canonicalize the ",
            False,
        ),
        (
            "any future encoding change is a contract bump per\nADR-0009 §4.2.",
            "any future encoding change is a contract bump.",
            False,
        ),
    ],
    "api/ci_scan_ext.py": [
        # Whole module docstring, rewritten clean (ticket/ADR refs removed,
        # technical content preserved).
        (
            r'\A""".*?"""',
            (
                '"""CI/CD scan-API extensions.\n'
                "\n"
                "Contract: ``are-scanner-api-v1`` v1.7.0 (additive minor over v1.3.0).\n"
                "\n"
                "Design notes:\n"
                "\n"
                "* Underwriter-approved item-5 ``ci_plugin`` output shape (categorized\n"
                "  ``failed``/``passed`` + ``band`` + ``disposition`` + ``dashboard_url``, no\n"
                "  prompt text; ``uw``/``customer`` byte-identical).\n"
                "* Default-gate threshold source = API lookup against the vendor's BackBond\n"
                "  account (single source of truth, tamper-resistant); ``fail_below_score``\n"
                "  remains a *stricter*-only override.\n"
                "\n"
                "Scope of THIS module (the pure-I/O, zero-scoring-impact legs — golden N=5\n"
                "delta = 0 by construction):\n"
                "\n"
                "* **Item 2** — outbound HMAC-SHA256 completion webhook signing +\n"
                "  fail-closed secret resolution.\n"
                "* **Item 3** — infra-vs-score error taxonomy (``error_class`` +\n"
                "  ``disposition``): transport failures map to ``infra`` (retry), a completed\n"
                "  low-score gate-fail maps to ``score`` (block); a never-completed scan is\n"
                "  **never** ``gate_pass``.\n"
                "* **Item 5 (adapter)** — the no-prompt ``ci_plugin`` categorized result\n"
                "  projection (pure function over a finalized score envelope). Prompts are\n"
                "  absent by construction; the projection only reads dimension ids, raws,\n"
                "  band, and a dashboard URL.\n"
                "\n"
                "The module is deliberately self-contained (no import of the hot\n"
                "``scan_handlers`` scoring path) so it is Tier-2 revertible (drop the module\n"
                "+ its wiring; the shipped routes are untouched). Wiring into ``api/app.py``\n"
                "/ ``api/scan_handlers.py`` (behind ``ARE_SCAN_TIER`` / ``ARE_SCAN_WEBHOOK``),\n"
                "the smoke-tier probe subset (item 1), the spine ``probe_set_hash`` column\n"
                "(item 4), and the threshold-source API-lookup that supplies\n"
                "``band``/``disposition`` for the live GET wiring (item 5) land under the\n"
                "same branch before the v1.7.0 merge.\n"
                '"""'
            ),
            True,
        ),
        # Inline `#:` / prose refs left in the body.
        (
            "#: Response/header names per ADR-0042 §2. ``X-BackBond-Signature``",
            "#: Response/header names. ``X-BackBond-Signature``",
            False,
        ),
        (
            "secret; per ADR-0042 §2 the ``webhook`` block carries only a",
            "secret; the ``webhook`` block carries only a",
            False,
        ),
        (
            "so both are ``infra`` (retry) per ADR-0042 §3 — a 4xx from",
            "so both are ``infra`` (retry) — a 4xx from",
            False,
        ),
        (
            "Invariant (ADR-0042 §3): a scan that never completed",
            "Invariant: a scan that never completed",
            False,
        ),
        (
            "#: API-lookup threshold source (BEAAA-11725 ruling; wired in the live GET",
            "#: API-lookup threshold source (wired in the live GET",
            False,
        ),
        (
            'raws" per ADR-0042 §5 AC (b).',
            'raws".',
            False,
        ),
    ],
    "ci_plugin/__init__.py": [
        (
            '"""BackBond OSS CI/CD plugin (WS-5, BEAAA-11729).',
            '"""BackBond OSS CI/CD plugin.',
            False,
        ),
        (
            "Design authority: WS-5 frozen design on BEAAA-11715; contract\n"
            "``are-scanner-api-v1`` v1.7.0 (WS-4 / BEAAA-11714).",
            "Contract: ``are-scanner-api-v1`` v1.7.0.",
            False,
        ),
        (
            "template is a separate, gated CTO action (BEAAA-11755) requiring the CEO\n"
            "release gate + Founder confirmation on BEAAA-11710. This package never",
            "template is a separate, gated release step. This package never",
            False,
        ),
    ],
    "ci_plugin/action.yml": [
        (
            "# BackBond Scanner — GitHub Action (WS-5, BEAAA-11729).",
            "# BackBond Scanner — GitHub Action.",
            False,
        ),
        (
            "# CTO action (BEAAA-11755) requiring the CEO release gate + Founder\n"
            "# confirmation on BEAAA-11710. Until then this file is consumed only from",
            "# release step. Until then this file is consumed only from",
            False,
        ),
    ],
    "ci_plugin/templates/.gitlab-ci.yml": [
        (
            "# BackBond Scanner — GitLab CI include template (WS-5, BEAAA-11729).",
            "# BackBond Scanner — GitLab CI include template.",
            False,
        ),
        (
            "# External publish is gated on BEAAA-11755 (CEO release gate + Founder\n"
            "# confirm on BEAAA-11710).",
            "# External publish is a separate, gated release step.",
            False,
        ),
    ],
    "ci_plugin/Dockerfile": [
        (
            "# Action and the GitLab CI template (WS-5, BEAAA-11729).",
            "# Action and the GitLab CI template.",
            False,
        ),
    ],
    "ci_plugin/README.md": [
        (
            "> Marketplace or the GitLab CI/CD Catalog. External release is gated\n"
            "> (BEAAA-11755 → CEO release gate + Founder confirmation on BEAAA-11710).",
            "> Marketplace or the GitLab CI/CD Catalog. External release is a\n"
            "> separate, gated step.",
            False,
        ),
        (
            "python -m pytest tests/test_ci_plugin_beaaa11729.py -q",
            "python -m pytest tests/test_ci_plugin.py -q",
            False,
        ),
    ],
    "ci_plugin/client.py": [
        (
            "# contracted band. The authoritative threshold source is the API lookup\n"
            "# (BEAAA-11725); this map is the documented last resort.",
            "# contracted band. The authoritative threshold source is the API lookup;\n"
            "# this map is the documented last resort.",
            False,
        ),
    ],
    "ci_plugin/gate.py": [
        (
            "matrix (BEAAA-11729) exercises every exit path without a network.",
            "matrix exercises every exit path without a network.",
            False,
        ),
        (
            "Exit-code semantics (WS-5 frozen design on BEAAA-11715):",
            "Exit-code semantics:",
            False,
        ),
        (
            "    Per the BEAAA-11725 ruling, the vendor's contracted policy band (looked",
            "    The vendor's contracted policy band (looked",
            False,
        ),
    ],
    "ci_plugin/runner.py": [
        (
            "    BEAAA-11725 API-lookup source of truth); falls back to the contract band",
            "    API-lookup source of truth); falls back to the contract band",
            False,
        ),
    ],
}


def _apply_rewrites(root: Path) -> list[str]:
    """Apply every configured rewrite; return a list of applied-rule tags."""
    applied: list[str] = []
    for rel, rules in REWRITES.items():
        path = root / rel
        if not path.exists():
            raise SystemExit(f"ERROR: expected shipping file missing: {rel}")
        text = path.read_text(encoding="utf-8")
        for i, (find, repl, is_regex) in enumerate(rules):
            if is_regex:
                new_text, n = re.subn(find, repl, text, flags=re.DOTALL)
            else:
                n = text.count(find)
                new_text = text.replace(find, repl)
            if n == 0:
                raise SystemExit(
                    f"ERROR: rule {rel}#{i} matched nothing (source drift?). "
                    f"find={find[:60]!r}..."
                )
            text = new_text
            applied.append(f"{rel}#{i} (x{n})")
        path.write_text(text, encoding="utf-8")
    return applied


def _gate(root: Path) -> list[str]:
    """Return a list of 'path:line: <label> :: <text>' hits (empty == clean)."""
    hits: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue  # binary asset — nothing textual to leak
        rel = path.relative_to(root)
        for lineno, line in enumerate(lines, 1):
            for label, pat in FORBIDDEN:
                if pat.search(line):
                    hits.append(f"{rel}:{lineno}: [{label}] {line.strip()}")
    return hits


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in {"scrub", "gate"}:
        print("usage: scrub.py {scrub|gate} <export-root>", file=sys.stderr)
        return 2
    cmd, root = argv[0], Path(argv[1])
    if not root.is_dir():
        print(f"ERROR: not a directory: {root}", file=sys.stderr)
        return 2

    if cmd == "scrub":
        applied = _apply_rewrites(root)
        print(f"scrub: applied {len(applied)} rewrite rules")
        hits = _gate(root)
        if hits:
            print("GATE FAILED after scrub — residual forbidden tokens:", file=sys.stderr)
            for h in hits:
                print("  " + h, file=sys.stderr)
            return 1
        print("gate: CLEAN (0 forbidden tokens)")
        return 0

    # cmd == "gate"
    hits = _gate(root)
    if hits:
        print(f"GATE FAILED — {len(hits)} forbidden token(s):", file=sys.stderr)
        for h in hits:
            print("  " + h, file=sys.stderr)
        return 1
    print("gate: CLEAN (0 forbidden tokens)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
