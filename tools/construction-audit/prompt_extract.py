#!/usr/bin/env python3
"""Extract only the text that actually reaches the target, for BEAAA-35660.

Checkpoint 1 scanned whole source files, so a ``probe_id:`` key sitting in a
YAML mapping matched even though that text never reaches the model. This module
narrows extraction to two things:

* ``python_prompt_literals`` — string literals in constructor code that are
  bound to a prompt-bearing name, dict key, or keyword argument. f-strings are
  rendered with their placeholders intact (``{probe_id}``) because the
  placeholder is exactly what S1 looks for.
* ``record_prompt_text`` — values in a corpus record whose key path is
  prompt-bearing (prompt, scenario, input, message, attack, ...).

Anything outside those two sets is registry bookkeeping and is not construction.
"""
from __future__ import annotations

import ast
import re
from typing import Any, Iterator

PROMPT_NAMES = re.compile(
    r"(prompt|message|messages|system|content|instruction|user_msg|user_text|query|turn)",
    re.IGNORECASE,
)
PROMPT_KEYS = re.compile(
    r"^(prompt|prompts|scenario|scenarios|input|inputs|user|user_prompt|system_prompt|"
    r"message|messages|content|text|attack|attack_text|injection|payload|instruction|"
    r"instructions|question|turn|turns|utterance|body|seed|template)$",
    re.IGNORECASE,
)


def _render(node: ast.AST) -> str | None:
    """Render a string-ish AST node, keeping f-string placeholders visible."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                out.append(part.value)
            elif isinstance(part, ast.FormattedValue):
                try:
                    out.append("{" + ast.unparse(part.value) + "}")
                except Exception:
                    out.append("{...}")
        return "".join(out)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _render(node.left), _render(node.right)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, (ast.Tuple, ast.List)):
        parts = [_render(e) for e in node.elts]
        if all(p is not None for p in parts):
            return "".join(p for p in parts if p)
    return None


JUDGE_MARKERS = re.compile(
    r"(judge|grader|grading|scorer|scoring_prompt|rubric_prompt|evaluator|referee|rater|adjudicat)",
    re.IGNORECASE,
)


def classify_channel(bound_to: str, enclosing: str, text: str) -> str:
    """target = the text the scanned agent reads; judge = the grader's own prompt.

    BEAAA-35020 is a defect in what the TARGET is told. A judge prompt naming the
    scanner is the grader knowing its own rubric, which is not the same finding
    and must not be counted as one.
    """
    if JUDGE_MARKERS.search(bound_to or "") or JUDGE_MARKERS.search(enclosing or ""):
        return "judge"
    if re.match(r"\s*you are (a|an|the) [^.]{0,60}(judge|grader|scorer|evaluator)", text, re.IGNORECASE):
        return "judge"
    return "target"


def python_prompt_literals(source: str) -> list[dict]:
    """Return prompt-bearing string literals, each tagged with its channel."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    found: list[dict] = []
    scope: list[str] = []

    parent_func: dict[int, str] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(fn):
                parent_func.setdefault(id(child), fn.name)

    def take(node: ast.AST, bound_to: str = "") -> None:
        text = _render(node)
        if text and len(text.strip()) > 1:
            enclosing = parent_func.get(id(node), "")
            found.append({
                "text": text,
                "bound_to": bound_to,
                "enclosing": enclosing,
                "channel": classify_channel(bound_to, enclosing, text),
            })

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            names += [
                t.attr for t in node.targets if isinstance(t, ast.Attribute)
            ]
            if any(PROMPT_NAMES.search(n) for n in names):
                take(node.value, ",".join(names))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            tgt = node.target
            name = tgt.id if isinstance(tgt, ast.Name) else getattr(tgt, "attr", "")
            if PROMPT_NAMES.search(name or ""):
                take(node.value, name or "")
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and PROMPT_KEYS.match(key.value)
                ):
                    take(value, key.value)
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg and PROMPT_NAMES.search(kw.arg):
                    take(kw.value, kw.arg)

    return found


def record_prompt_text(record: Any, _depth: int = 0) -> Iterator[str]:
    """Yield prompt-bearing strings from a corpus record (dict/list/scalar)."""
    if _depth > 8:
        return
    if isinstance(record, dict):
        for key, value in record.items():
            if isinstance(key, str) and PROMPT_KEYS.match(key):
                if isinstance(value, str):
                    yield value
                else:
                    yield from _all_strings(value, 0)
            else:
                yield from record_prompt_text(value, _depth + 1)
    elif isinstance(record, list):
        for item in record:
            yield from record_prompt_text(item, _depth + 1)


def _all_strings(node: Any, depth: int) -> Iterator[str]:
    if depth > 8:
        return
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _all_strings(value, depth + 1)
    elif isinstance(node, list):
        for item in node:
            yield from _all_strings(item, depth + 1)
