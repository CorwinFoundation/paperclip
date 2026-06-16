"""Byte-equality of the post-bind floor-pattern list vs the are-connector source.

CTO brief §3 non-negotiable: the post-bind ingest package may not import
`tools/are-connector/` at runtime (Auditor import-edge check). Instead, the
secret-family `SECRET_PATTERNS` list is copy-and-adapt at the helper level.
This test reads both source files and asserts byte-equality on the regex
pattern strings + flags. If the are-connector source moves, this test fails
CI-loud and the post-bind copy MUST be updated in the same PR (Schema
§Redaction policy classes — Floor non-relaxation).

The connector lives at `tools/are-connector/src/are_connector/redaction.py`
per BEAAA-5816 §Status header A2 symbol fix. The extraction here uses AST
parsing so the test is robust to surrounding code drift (no fragile
bracket-matching on raw text).
"""

from __future__ import annotations

import ast
import re

import pytest

from _test_paths import REPO_ROOT
from post_bind_ingest.normalizer.redaction import (
    SECRET_FAMILY_REPLACEMENT,
    SECRET_PATTERNS,
    floor_scrub_secrets,
)


CONNECTOR_REDACTION = (
    REPO_ROOT / "tools" / "are-connector" / "src" / "are_connector" / "redaction.py"
)


def _extract_secret_patterns_via_ast() -> list[tuple[str, int]]:
    """Return [(pattern_string, flags_int), ...] from the connector source.

    Uses AST so we don't execute connector code or its sibling imports. The
    connector module declares `SECRET_PATTERNS = [re.compile(r"..."), ...]` at
    module scope; each list element is an `ast.Call` to `re.compile` whose
    first argument is a string literal and optional second argument is the
    flags (either a single `ast.Attribute` like `re.S` or an `ast.BinOp` of
    `re.X | re.Y`).
    """
    if not CONNECTOR_REDACTION.exists():
        pytest.skip(
            f"are-connector source not present at {CONNECTOR_REDACTION}; drift check is "
            "advisory-only in this environment. CI must run on a checkout where the "
            "connector is materialized."
        )
    source = CONNECTOR_REDACTION.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(CONNECTOR_REDACTION))
    target: list[ast.expr] | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "SECRET_PATTERNS":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        target = list(node.value.elts)
                        break
        if target is not None:
            break
    if target is None:
        pytest.fail("connector redaction.py no longer defines SECRET_PATTERNS at module scope")
    out: list[tuple[str, int]] = []
    for index, elem in enumerate(target):
        if not isinstance(elem, ast.Call):
            pytest.fail(f"SECRET_PATTERNS[{index}] is not a function call")
        if not (
            isinstance(elem.func, ast.Attribute)
            and isinstance(elem.func.value, ast.Name)
            and elem.func.value.id == "re"
            and elem.func.attr == "compile"
        ):
            pytest.fail(f"SECRET_PATTERNS[{index}] is not re.compile(...)")
        if not elem.args or not isinstance(elem.args[0], ast.Constant) or not isinstance(elem.args[0].value, str):
            pytest.fail(f"SECRET_PATTERNS[{index}] first arg is not a string literal")
        pattern = elem.args[0].value
        flags = _eval_flags(elem.args[1]) if len(elem.args) >= 2 else 0
        out.append((pattern, flags))
    return out


def _eval_flags(node: ast.expr) -> int:
    """Evaluate `re.<FLAG>` and `re.X | re.Y` against the `re` module."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "re":
        return getattr(re, node.attr)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _eval_flags(node.left) | _eval_flags(node.right)
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    pytest.fail(f"unsupported re.compile flags AST: {ast.dump(node)}")


def test_secret_patterns_byte_equal_to_connector() -> None:
    connector = _extract_secret_patterns_via_ast()
    assert len(SECRET_PATTERNS) == len(connector), (
        f"length drift: post-bind has {len(SECRET_PATTERNS)} patterns, "
        f"connector has {len(connector)}"
    )
    for index, (post, (conn_pat, conn_flags)) in enumerate(zip(SECRET_PATTERNS, connector)):
        assert post.pattern == conn_pat, (
            f"pattern[{index}] string drift: post-bind={post.pattern!r} vs "
            f"connector={conn_pat!r}. If the connector moved, copy the new "
            f"pattern into tools/post-bind-ingest/src/post_bind_ingest/normalizer/"
            f"redaction.py::SECRET_PATTERNS in the same PR."
        )
        # Compile the connector source-side pattern under the same explicit
        # flags it declared, then compare the post-compile `.flags` attribute
        # on both sides. This normalizes inline (?i)/(?s) tokens that show up
        # as IGNORECASE / DOTALL after compilation but are not visible at the
        # AST `Call.args` level.
        connector_compiled = re.compile(conn_pat, conn_flags)
        assert post.flags == connector_compiled.flags, (
            f"pattern[{index}] effective flags drift: post-bind={post.flags} vs "
            f"connector={connector_compiled.flags}"
        )


def test_replacement_token_matches_schema_contract() -> None:
    """Schema §Redaction policy classes — Floor rule: token is `[REDACTED:secret_family]`."""
    out, _ = floor_scrub_secrets("AKIAIOSFODNN7EXAMPLE")
    assert SECRET_FAMILY_REPLACEMENT == "[REDACTED:secret_family]"
    assert out == "[REDACTED:secret_family]"
