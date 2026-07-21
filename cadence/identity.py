"""Deterministic candidate identity + normalization (BEAAA-16530 / BEAAA-16536).

Implements the identity and deduplication rule from
``artifacts/beaaa-16530/continuous-live-test-cadence-v0.1.md``:

  * ``case_id_hash   = sha256(stream + "\\n" + family + "\\n" + case_id)``
  * ``semantic_hash  = sha256(norm_prompt_or_action + "\\n" + norm_expected + "\\n" + norm_oracle)``

Normalization is UTF-8 NFC; CRLF -> LF; leading/trailing whitespace removed;
internal whitespace runs *outside quoted literals* collapse to one ASCII space;
structured actions/oracles use canonical JSON (sorted keys, tight separators);
free text is not case-folded. This module owns the single implementation so no
stream builder duplicates hashing logic (AGENTS.md no-duplicate-logic rule).
"""
from __future__ import annotations

import hashlib
import unicodedata
from typing import Any

from determination.rule_dsl import canonical_json_bytes


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def normalize_text(text: str) -> str:
    """Normalize free text per the cadence identity rule.

    Collapse only whitespace that sits *outside* single- or double-quoted
    literals so a quoted secret / literal is never silently reshaped.
    """
    text = _nfc(text).replace("\r\n", "\n").replace("\r", "\n").strip()
    out: list[str] = []
    quote: str | None = None
    pending_ws = False
    for ch in text:
        if quote is not None:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            if pending_ws:
                out.append(" ")
                pending_ws = False
            out.append(ch)
            quote = ch
            continue
        if ch.isspace():
            pending_ws = True
            continue
        if pending_ws:
            out.append(" ")
            pending_ws = False
        out.append(ch)
    return "".join(out)


def canonical_json(value: Any) -> str:
    """Canonical JSON string (sorted keys, tight separators, UTF-8 preserved)."""
    return canonical_json_bytes(value).decode("utf-8")


def _sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def case_id_hash(stream: str, family: str, case_id: str) -> str:
    preimage = "\n".join(
        (normalize_text(stream), normalize_text(family), normalize_text(case_id))
    )
    return _sha256_hex(preimage.encode("utf-8"))


def semantic_hash(
    prompt_or_action: str, expected_behavior: str, oracle: Any
) -> str:
    """Semantic identity over materialized input, expectation, and oracle.

    ``oracle`` must carry verdict semantics, applicability conditions, and
    threshold/version pins (enforced by the caller). Structured oracles are
    canonicalized before normalization so key order can never split identity.
    """
    parts = (
        normalize_text(prompt_or_action),
        normalize_text(expected_behavior),
        normalize_text(
            oracle if isinstance(oracle, str) else canonical_json(oracle)
        ),
    )
    return _sha256_hex("\n".join(parts).encode("utf-8"))


def source_sha256(path: str) -> str:
    with open(path, "rb") as handle:
        return _sha256_hex(handle.read())
