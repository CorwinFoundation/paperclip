"""RFC 8785 JSON Canonicalization Scheme (JCS) — at-write-time snapshot_hash preimage.

BEAAA-1271 / ADR-0009 §4.2. Used to canonicalize the `score.json` envelope
prior to `canonical_sha256` so the persisted `snapshot_hash` is byte-stable
across permutations of input key order and non-significant whitespace, and
remains recomputable byte-for-byte from the persisted bundle bytes by any
consumer that re-canonicalizes the same JSON value.

This implementation is the JCS subset that the score-envelope value space
exercises: nulls, booleans, integers, IEEE-754 doubles, strings, arrays,
and objects whose keys are strings. JCS specifies:

  - Object members sorted by the UTF-16 code-unit ordering of their keys
    (RFC 8785 §3.2.3).
  - No insignificant whitespace; element / member separators are bare
    `,` and `:` (RFC 8785 §3.2.4).
  - Strings emitted with the minimal-escape rule of RFC 8785 §3.2.2.5 —
    only the seven mandatory short escapes (`\\\"`, `\\\\`, `\\b`, `\\f`,
    `\\n`, `\\r`, `\\t`) plus `\\u00xx` for the remaining C0 control codes;
    every other code point emitted as-is. Hex digits are lowercase.
  - Numbers serialized per ECMAScript 6 §7.1.12.1 "ToString applied to the
    Number Type" (RFC 8785 §3.2.2.3). For integers in the safe-integer
    range we emit the decimal integer with no fractional part; non-finite
    floats are rejected (JSON does not represent them and a snapshot-hash
    preimage with NaN would be ill-defined).

Pinned at v1.2.0; any future encoding change is a contract bump per
ADR-0009 §4.2.
"""
from __future__ import annotations

import math
import re
from typing import Any

_SHORT_ESCAPES = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
    0x22: '\\"',
    0x5C: "\\\\",
}


class JCSEncodeError(ValueError):
    """Raised when a value cannot be encoded under RFC 8785 JCS."""


def canonicalize(value: Any) -> bytes:
    """Return the RFC 8785 canonical JSON bytes for `value`.

    Top-level value may be any JSON value (null, bool, int, float, str,
    list, dict). Object keys must be strings. Floats must be finite.
    """
    out: list[str] = []
    _encode(value, out)
    return "".join(out).encode("utf-8")


def _encode(value: Any, out: list[str]) -> None:
    if value is None:
        out.append("null")
        return
    if value is True:
        out.append("true")
        return
    if value is False:
        out.append("false")
        return
    if isinstance(value, int) and not isinstance(value, bool):
        out.append(_encode_int(value))
        return
    if isinstance(value, float):
        out.append(_encode_float(value))
        return
    if isinstance(value, str):
        out.append(_encode_string(value))
        return
    if isinstance(value, (list, tuple)):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _encode(item, out)
        out.append("]")
        return
    if isinstance(value, dict):
        items = list(value.items())
        for k, _ in items:
            if not isinstance(k, str):
                raise JCSEncodeError(
                    f"object keys must be strings (got {type(k).__name__})"
                )
        # RFC 8785 §3.2.3: sort by UTF-16 code-unit sequence. CPython's
        # str-compare is by Unicode code point, which differs from UTF-16
        # only for code points >= U+10000 (those go through surrogate pairs
        # in UTF-16). For the score-envelope key space (ASCII identifiers)
        # the two orderings coincide; for full RFC compliance over arbitrary
        # keys we explicitly encode to UTF-16-BE bytes for the sort key.
        items.sort(key=lambda kv: kv[0].encode("utf-16-be"))
        out.append("{")
        for i, (k, v) in enumerate(items):
            if i:
                out.append(",")
            out.append(_encode_string(k))
            out.append(":")
            _encode(v, out)
        out.append("}")
        return
    raise JCSEncodeError(f"unsupported JSON value of type {type(value).__name__}")


def _encode_int(value: int) -> str:
    return str(value)


# Pattern that matches Python's repr() for a finite float in scientific form,
# used to bridge Python's float repr to ECMAScript 6 ToString.
_SCI_RE = re.compile(r"^(-?)(\d+)(?:\.(\d+))?[eE]([+-]?\d+)$")


def _encode_float(value: float) -> str:
    """Return the RFC 8785 ECMAScript 6 ToString representation of `value`.

    The score envelope today carries only integers and small-magnitude
    fixed-point floats; this implementation covers that space exactly and
    rejects the corners JCS does not permit (NaN, ±Inf).
    """
    if not math.isfinite(value):
        raise JCSEncodeError(f"non-finite float not representable in JCS: {value!r}")
    # Integer-valued floats: ECMAScript emits the integer form without a
    # trailing ".0" — JCS §3.2.2.3 example: 1.0 → "1". This matters because
    # `json.dumps(1.0)` returns "1.0" which would mis-canonicalize.
    if value == int(value) and abs(value) < 1e21:
        return str(int(value))
    # Python's repr produces the shortest round-trip decimal for a finite
    # float; we then convert any scientific-notation form to ECMAScript's
    # exponent rules. ECMAScript uses "e+N" / "e-N" (lowercase e, sign
    # always present on the exponent) and drops the explicit ".0" on the
    # mantissa when it would be redundant.
    text = repr(value)
    m = _SCI_RE.match(text)
    if m is None:
        return text
    sign, int_part, frac_part, exp = m.groups()
    exp_n = int(exp)
    mantissa = int_part
    if frac_part:
        mantissa = f"{int_part}.{frac_part}"
    exp_str = f"e+{exp_n}" if exp_n >= 0 else f"e{exp_n}"
    return f"{sign}{mantissa}{exp_str}"


def _encode_string(value: str) -> str:
    parts: list[str] = ['"']
    for ch in value:
        cp = ord(ch)
        if cp in _SHORT_ESCAPES:
            parts.append(_SHORT_ESCAPES[cp])
        elif cp < 0x20:
            parts.append(f"\\u{cp:04x}")
        else:
            parts.append(ch)
    parts.append('"')
    return "".join(parts)


__all__ = ["canonicalize", "JCSEncodeError"]
