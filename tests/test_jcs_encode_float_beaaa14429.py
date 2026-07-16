"""BEAAA-14429 [F9 impl] — RFC 8785 / ECMA-262 Number::toString conformance
for ``api.jcs._encode_float``.

Parent BEAAA-14410 [Code Review F9]. CTO ruling: RFC-conformance bug-fix
(no Underwriter pricing gate, no rubric change). This suite is the merge
gate for the fix.

The bug (canon origin/master@7f2dc613): for finite non-integer floats whose
ECMA-262 decimal exponent ``n`` lands in the fixed-decimal range (-6, 21]
but whose Python ``repr`` uses scientific notation (|x| in [1e-6, 1e-4)),
the old ``_SCI_RE`` branch emitted ECMAScript *scientific* form instead of
the required *decimal* form (``1e-5`` instead of ``0.00001``). RFC 8785
§3.2.2.3 defers to ECMA-262 Number::toString, which uses decimal notation
for ``n`` in (-6, 21] and scientific only for ``n <= -6`` or ``n > 21``.

A second, latent divergence surfaced by the AC-2 oracle cross-check (below):
the previous ``str(int(value))`` integer fast path was gated on
``abs(value) < 1e21``, but at/above ``2**53`` the exact integer carries more
significant digits than ECMA-262's shortest round-trip form (e.g.
``1.3038e20`` → exact ``...388800`` vs ECMA ``...390000``). The fix bounds
that fast path to the safe-integer range (``< 2**53``) and routes larger
integer-valued floats through the general algorithm, which reproduces
ECMA-262 exactly. Real score envelopes never carry such magnitudes (see the
AC-3 real-envelope hash-drift check, Δ=0), so this is a latent-conformance
correction, not an emitted-hash change on production data.

Any intentional change to the float encoding is a contract bump per
ADR-0009 §4.2 / ADR-0017 §2.3; this suite pins the conformant behavior.
"""
from __future__ import annotations

import json
import math
import pathlib
import random
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.jcs import JCSEncodeError, _encode_float  # noqa: E402

# ECMA-262 Number::toString oracle, generated once with Node v22.22.0 via
# `String(Number(reprString))` for a seeded sample (see
# tests/fixtures/jcs_float/generate_oracle.py). Committed so the cross-check
# needs no Node at test time.
ORACLE_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "jcs_float"
    / "ecma262_number_tostring_oracle.json"
)


class EncodeFloatConformanceVectorTests(unittest.TestCase):
    """AC-1: exact output for the RFC 8785 / ECMA-262 reference vectors."""

    # (Python float, required JCS output). Includes the ticket's concrete
    # sub-1e-4 failures, the band boundaries, both scientific tails, denormal
    # min, large magnitudes, the 1e20/1e21 fixed<->scientific boundary, and
    # the signed-zero / integer-valued cases.
    VECTORS = [
        (1e-5, "0.00001"),
        (1e-6, "0.000001"),
        (2.5e-5, "0.000025"),
        (9.999999e-5, "0.00009999999"),
        (1e-4, "0.0001"),
        (1e-7, "1e-7"),
        (5e-324, "5e-324"),          # IEEE-754 denormal min
        (1.5e30, "1.5e+30"),
        (1e21, "1e+21"),             # first magnitude that is scientific
        (1e20, "100000000000000000000"),  # last fixed-form magnitude
        (123.45, "123.45"),
        (0.5, "0.5"),
        (100.06, "100.06"),
        (-0.0, "0"),
        (0.0, "0"),
        # ECMA-262 / RFC 8785 reference spot values.
        (1.0, "1"),
        (-1.0, "-1"),
        (-1.5, "-1.5"),
        (5e-6, "0.000005"),
        (9.999999999999999e22, "1e+23"),  # rounds to the double 1e23
    ]

    def test_conformance_vectors(self) -> None:
        for value, expected in self.VECTORS:
            with self.subTest(value=repr(value)):
                self.assertEqual(_encode_float(value), expected)

    def test_non_finite_rejected(self) -> None:
        for bad in (math.inf, -math.inf, math.nan):
            with self.subTest(value=repr(bad)):
                with self.assertRaises(JCSEncodeError):
                    _encode_float(bad)


class EncodeFloatOracleAndRoundTripTests(unittest.TestCase):
    """AC-2: byte-for-byte match vs the committed ECMA-262 oracle over a
    seeded sample (incl. the [1e-6, 1e-4) band and large magnitudes), plus
    the shortest-round-trip property ``float(_encode_float(x)) == x``."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.oracle = json.loads(ORACLE_PATH.read_text(encoding="utf-8"))
        # Guard against a truncated/empty fixture silently passing.
        assert len(cls.oracle) >= 1000, f"oracle too small: {len(cls.oracle)}"

    def test_matches_ecmascript_oracle(self) -> None:
        mismatches = []
        for entry in self.oracle:
            x = float(entry["input"])
            got = _encode_float(x)
            if got != entry["ecma"]:
                mismatches.append((entry["input"], got, entry["ecma"]))
        self.assertEqual(
            mismatches, [],
            f"{len(mismatches)} value(s) diverge from ECMA-262 Number::toString; "
            f"first few: {mismatches[:5]}",
        )

    def test_shortest_round_trip_preserved(self) -> None:
        failures = []
        for entry in self.oracle:
            x = float(entry["input"])
            got = _encode_float(x)
            if float(got) != x:
                failures.append((entry["input"], got))
        self.assertEqual(failures, [], f"round-trip failures: {failures[:5]}")

    def test_seeded_band_round_trip(self) -> None:
        # Independent seeded resample concentrated on the fixed<->scientific
        # boundaries, asserting round-trip and the fixed/scientific split.
        rng = random.Random(14429)
        for _ in range(2000):
            e = rng.randint(-8, 24)
            m = rng.uniform(1.0, 10.0)
            x = m * (10.0 ** e)
            if not math.isfinite(x):
                continue
            if rng.random() < 0.5:
                x = -x
            got = _encode_float(x)
            self.assertEqual(float(got), x, f"round-trip lost for {x!r} -> {got}")


if __name__ == "__main__":
    unittest.main()
