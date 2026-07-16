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


def _encode_float(value: float) -> str:
    """Return the RFC 8785 ECMAScript 6 ToString representation of `value`.

    RFC 8785 §3.2.2.3 defers number serialization to ECMA-262
    Number::toString. That algorithm picks the shortest decimal string
    that round-trips to the same IEEE-754 double, then formats it in one of
    two ways keyed on the decimal exponent `n` (the position of the point
    relative to the significant digits `s`, which has `k` digits):

      * fixed-decimal notation when `n` is in (-6, 21];
      * scientific notation (``e+N`` / ``e-N``, lowercase ``e``, sign always
        present on the exponent) when `n <= -6` or `n > 21`.

    Python's ``repr`` already gives us the shortest round-trip digit string,
    so we recover `s`, `k`, `n` from it and apply the ECMA-262 branch table
    directly. This fixes the previous repr-passthrough bug where sub-1e-4
    magnitudes (e.g. ``1e-5``) whose Python repr is scientific were emitted
    in scientific form even though ECMA-262 requires fixed decimal there
    (``0.00001``) — a difference that changed the canonical bytes and thus
    the ``snapshot_hash`` an external re-canonicalizer would compute.

    The integer-valued fast path is retained: ECMAScript emits the integer
    form without a trailing ``.0`` — JCS §3.2.2.3 example: ``1.0`` → ``"1"``,
    ``-0.0`` → ``"0"``. Non-finite floats are rejected (JSON cannot
    represent them and a snapshot-hash preimage with NaN is ill-defined).
    """
    if not math.isfinite(value):
        raise JCSEncodeError(f"non-finite float not representable in JCS: {value!r}")
    # Integer-valued floats in the safe-integer range (|x| < 2**53) render
    # identically via their exact integer: below 2**53 every integer is
    # exactly representable and the shortest round-trip decimal ECMA-262
    # would pick IS that integer, so `str(int(value))` == Number::toString.
    # `json.dumps(1.0)` returns "1.0", which would otherwise mis-canonicalize.
    # NOTE: at/above 2**53 the exact integer has MORE significant digits than
    # the shortest round-trip form ECMA-262 emits (e.g. 1.3038e20 → exact
    # ...388800 vs ECMA ...390000), so those integer-valued floats MUST fall
    # through to the general algorithm below, not the exact-int fast path.
    if value == int(value) and abs(value) < 2**53:
        return str(int(value))

    sign = "-" if value < 0 else ""
    text = repr(abs(value)).replace("E", "e")
    if "e" in text:
        mant, _, exp = text.partition("e")
        exp_n = int(exp)
    else:
        mant, exp_n = text, 0
    int_p, _, frac_p = mant.partition(".")
    digits = int_p + frac_p
    # `point` = number of digits that sit left of the decimal point within
    # `digits` before any zero-stripping.
    point = len(int_p) + exp_n
    stripped = digits.lstrip("0")
    point -= len(digits) - len(stripped)  # stripping leading zeros shifts the point left
    digits = stripped.rstrip("0")  # trailing zeros are not significant digits
    if not digits:
        return "0"
    k = len(digits)  # ECMA-262: number of significant digits in `s`
    n = point  # ECMA-262: decimal exponent

    if k <= n <= 21:
        # All significant digits are integral; pad with zeros to reach `n`.
        return sign + digits + "0" * (n - k)
    if 0 < n <= 21:
        # Point falls inside the significant digits.
        return sign + digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        # Leading "0." plus `-n` zeros before the significant digits.
        return sign + "0." + "0" * (-n) + digits
    # Scientific notation: single leading digit, exponent e = n - 1.
    e = n - 1
    exp_str = f"e+{e}" if e >= 0 else f"e-{-e}"
    mantissa = digits if k == 1 else digits[0] + "." + digits[1:]
    return sign + mantissa + exp_str


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
