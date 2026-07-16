#!/usr/bin/env python3
"""Regenerate the ECMA-262 Number::toString oracle for BEAAA-14429.

The committed fixture ``ecma262_number_tostring_oracle.json`` is the
known-good cross-check for ``api.jcs._encode_float`` (RFC 8785 §3.2.2.3).
It is generated ONCE and committed so the test suite needs no Node at run
time. Regenerate only when intentionally revising the seeded sample; any
resulting behavioral change to the encoder is a contract bump per
ADR-0009 §4.2 / ADR-0017 §2.3.

Oracle is Node's ``String(Number(x))`` — the reference ECMA-262
Number::toString. Pinned to Node v22.22.0 (bump this comment if you
regenerate on a newer Node; the algorithm is stable across LTS lines).

Usage:
    python3 tests/fixtures/jcs_float/generate_oracle.py

Requires ``node`` on PATH. Deterministic (seeded), so a clean regeneration
on the same Node produces a byte-identical fixture.
"""
from __future__ import annotations

import json
import math
import pathlib
import random
import struct
import subprocess

SEED = 84291  # BEAAA-14429
HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "ecma262_number_tostring_oracle.json"


def sample_inputs() -> list[str]:
    rng = random.Random(SEED)
    vals: set[float] = set()

    explicit = [
        1e-5, 1e-6, 2.5e-5, 9.999999e-5, 1e-4, 1e-7, 5e-324, 1.5e30,
        1e21, 1e20, 123.45, 0.5, 100.06, -0.0, 0.0, 1.0, -1.0, 3.14159,
        2.220446049250313e-16,
    ]
    vals.update(explicit)

    # Dense coverage of the affected [1e-6, 1e-4) band (ECMA-262 n in {-4,-5}).
    for _ in range(200):
        vals.add(rng.uniform(1.0, 9.999999) * (10.0 ** rng.choice([-6, -5])))

    # Broad magnitude sweep, both signs.
    for _ in range(400):
        x = rng.uniform(1.0, 10.0) * (10.0 ** rng.randint(-30, 30))
        vals.add(-x if rng.random() < 0.5 else x)

    # Fully random finite doubles via bit patterns.
    for _ in range(400):
        x = struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0]
        if math.isfinite(x):
            vals.add(x)

    # repr() is the shortest round-trip decimal; Node parses it to the same
    # IEEE-754 double (both use correctly-rounded shortest conversion).
    return sorted({repr(v) for v in vals})


def main() -> None:
    reprs = sample_inputs()
    node_src = (
        "const lines=require('fs').readFileSync(0,'utf8')"
        ".split('\\n').filter(s=>s.length);"
        "process.stdout.write(JSON.stringify("
        "lines.map(s=>({input:s,ecma:String(Number(s))})),null,1));"
    )
    result = subprocess.run(
        ["node", "-e", node_src],
        input="\n".join(reprs),
        capture_output=True,
        text=True,
        check=True,
    )
    OUT.write_text(result.stdout, encoding="utf-8")
    print(f"wrote {OUT} ({len(reprs)} entries)")


if __name__ == "__main__":
    main()
