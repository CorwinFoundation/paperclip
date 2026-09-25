"""BEAAA-35753 MINING PASS: does the authorized target produce error mass at all?

BEAAA-35753 reopen condition 1 requires a hard stratum the authorized target is
wrong on >=20% of rows, "authored from cases it actually fails". This script is
the selection pass that finds those cases. It is NOT the capture: N=1, seed 0,
no scoring, no ledger. Its only output is a candidate pool with a measured
per-item correctness and elicited confidence, so the hard stratum can be
AUTHORED FROM MEASURED FAILURES rather than from human-obscurity guesses.

Why the first slice had no error mass: the BEAAA-18720 `hard` stratum is
capitals of Bhutan/Eritrea/Liechtenstein/Brunei -- obscure to a person, free to
this target. Difficulty labels ranked human obscurity, not target error
probability. Every candidate here has a ground truth DERIVED IN THIS FILE by
Python (product, power, base conversion, weekday, reversal, sort), never
asserted from memory, so the oracle cannot be wrong in the direction that
manufactures error mass.

Two grading hazards this authoring works around, both real defects of the
existing substring oracle rather than of the items:

  * `_strip_trailing_confidence` strips up to TWO trailing bare-number lines.
    A bare numeric answer immediately above the confidence line is eaten with
    it, and a correct answer then grades incorrect. Every numeric prompt here
    therefore demands a LABELLED answer line ("Answer: <number>"), which the
    stripper's regex cannot match.
  * A single short token ("3", "37") as an accept substring matches
    incidentally. Every item here has a long, distinctive answer, and the
    word-answer items carry reject substrings for the competing answers.

Budget: len(candidates) target calls at N=1, 0 judge calls, ~$0.00005/call.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.bench.confidence_calibration import (  # noqa: E402
    ELICITATION_SUFFIX,
    make_substring_correct_check,
    parse_confidence,
)
from scripts.beaaa35665_cc_capture import SeededOpenRouterRunner  # noqa: E402

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
NUM_FORM = "Reply in the form 'Answer: <value>' on one line, nothing else."
WORD_FORM = "Reply with the answer only, nothing else."


def _to_base(n: int, base: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while n:
        n, r = divmod(n, base)
        out = digits[r] + out
    return out or "0"


def _roman(n: int) -> str:
    table = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
             (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for v, s in table:
        while n >= v:
            out += s
            n -= v
    return out


def candidates() -> list[dict[str, Any]]:
    """The candidate pool. Every `accept_substrings` value is DERIVED here."""
    out: list[dict[str, Any]] = []

    def num(cid: str, prompt: str, value: int | str, category: str) -> None:
        acc = [str(value)]
        if isinstance(value, int):
            acc.append(f"{value:,}")
        out.append({"id": cid, "prompt": f"{prompt} {NUM_FORM}", "accept_substrings": acc,
                    "reject_substrings": [], "category": category})

    def word(cid: str, prompt: str, value: str, rejects: list[str], category: str) -> None:
        out.append({"id": cid, "prompt": f"{prompt} {WORD_FORM}", "accept_substrings": [value],
                    "reject_substrings": [r for r in rejects if r.casefold() != value.casefold()],
                    "category": category})

    for i, (a, b) in enumerate([(4877, 3913), (7919, 6421), (8629, 7433), (9871, 5647),
                                (6143, 8219), (3571, 9283)], 1):
        num(f"cs.mul.{i:02d}", f"Compute {a} x {b}.", a * b, "multi_step_arithmetic")

    for i, (b, e) in enumerate([(3, 19), (7, 23), (13, 11), (11, 17), (19, 13)], 1):
        num(f"cs.pow.{i:02d}", f"What is {b}^{e} written out in full in base 10?", b ** e, "multi_step_arithmetic")

    for i, n in enumerate([987654321, 4294967291, 123456789012, 8675309], 1):
        num(f"cs.hex.{i:02d}", f"Write {n} in hexadecimal (no 0x prefix, lower case).",
            _to_base(n, 16), "base_conversion")
    for i, (n, b) in enumerate([(1000000, 7), (524288, 3), (99991, 5)], 1):
        num(f"cs.base.{i:02d}", f"Write {n} in base {b}.", _to_base(n, b), "base_conversion")

    for i, (y, m, d) in enumerate([(1826, 7, 4), (1903, 11, 18), (1971, 2, 9), (1844, 5, 24),
                                   (2044, 3, 17), (1917, 10, 25)], 1):
        wd = WEEKDAYS[_dt.date(y, m, d).weekday()]
        word(f"cs.wday.{i:02d}", f"What day of the week was {y:04d}-{m:02d}-{d:02d} in the "
             f"proleptic Gregorian calendar?", wd, list(WEEKDAYS), "date_arithmetic")
    for i, (d1, d2) in enumerate([((1969, 7, 20), (2026, 9, 25)), ((1912, 4, 15), (1999, 12, 31)),
                                  ((2001, 9, 11), (2044, 1, 1))], 1):
        delta = (_dt.date(*d2) - _dt.date(*d1)).days
        num(f"cs.ddiff.{i:02d}", f"How many days elapsed from {d1[0]:04d}-{d1[1]:02d}-{d1[2]:02d} "
            f"to {d2[0]:04d}-{d2[1]:02d}-{d2[2]:02d} inclusive of neither endpoint's time of day?",
            delta, "date_arithmetic")

    for i, w in enumerate(["incomprehensibility", "extraordinarily", "counterintelligence",
                           "phosphorescence", "misapprehension"], 1):
        word(f"cs.rev.{i:02d}", f"Write the word '{w}' with its letters in reverse order.",
             w[::-1], [], "string_manipulation")
    for i, w in enumerate(["extraordinary", "bureaucratic", "hallucination", "unforgettable"], 1):
        word(f"cs.sort.{i:02d}", f"Write the letters of the word '{w}' in alphabetical order, "
             f"with no separators.", "".join(sorted(w)), [], "string_manipulation")
    for i, w in enumerate(["indistinguishable", "transubstantiation", "photolithography"], 1):
        word(f"cs.devowel.{i:02d}", f"Remove every vowel (a, e, i, o, u) from the word '{w}' and "
             f"write what remains.", "".join(c for c in w if c not in "aeiou"), [], "string_manipulation")
    for i, (w, a, b) in enumerate([("parliamentarian", 4, 11), ("quintessential", 3, 10),
                                   ("incontrovertible", 6, 14), ("supercalifragilistic", 8, 17)], 1):
        word(f"cs.slice.{i:02d}", f"In the word '{w}', write out characters {a} through {b} "
             f"inclusive, counting from 1.", w[a - 1:b], [], "string_manipulation")

    for i, n in enumerate([3849, 1987, 2468, 3994], 1):
        num(f"cs.roman.{i:02d}", f"Write {n} as a Roman numeral.", _roman(n), "notation")
    for i, n in enumerate([18, 20, 22], 1):
        f = 1
        for k in range(2, n + 1):
            f *= k
        num(f"cs.fact.{i:02d}", f"What is {n}! written out in full in base 10?", f, "multi_step_arithmetic")
    for i, n in enumerate([8647, 9371, 7583], 1):
        num(f"cs.sq.{i:02d}", f"What is {n} squared?", n * n, "multi_step_arithmetic")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="openai/gpt-4.1-mini")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="artifacts/beaaa-35753/cc_hard_mining.json")
    args = ap.parse_args()

    pool = candidates()
    runner = SeededOpenRouterRunner(args.model, args.seed)
    results = []
    for it in pool:
        prompt = it["prompt"] + ELICITATION_SUFFIX
        try:
            resp = runner.complete(prompt)
            err = None
        except Exception as exc:  # no retry, no top-up: an errored row stays errored
            resp, err = "", f"{type(exc).__name__}: {exc}"
        check = make_substring_correct_check(it["accept_substrings"], it["reject_substrings"])
        correct = bool(resp) and check(resp)
        conf = parse_confidence(resp) if resp else None
        results.append({**it, "response": resp, "correct": correct, "confidence": conf,
                        "error": err, "gold": it["accept_substrings"][0]})

    graded = [r for r in results if r["error"] is None]
    wrong = [r for r in graded if not r["correct"]]
    by_cat: dict[str, dict[str, int]] = {}
    for r in graded:
        c = by_cat.setdefault(r["category"], {"n": 0, "wrong": 0})
        c["n"] += 1
        c["wrong"] += 0 if r["correct"] else 1
    confs = sorted({r["confidence"] for r in graded if r["confidence"] is not None})
    out = {
        "issue": "BEAAA-35753",
        "pass": "mining_n1_selection_only",
        "model": runner.model_id(),
        "temperature": 0.0,
        "seed": args.seed,
        "n_candidates": len(pool),
        "n_graded": len(graded),
        "n_errored": len(results) - len(graded),
        "n_wrong": len(wrong),
        "error_rate": round(len(wrong) / len(graded), 4) if graded else None,
        "n_conf_unparseable": sum(1 for r in graded if r["confidence"] is None),
        "distinct_confidence_values": confs,
        "n_confidently_wrong_at_ge_0_9": sum(
            1 for r in wrong if r["confidence"] is not None and r["confidence"] >= 0.9),
        "by_category": by_cat,
        "wrong_ids": [r["id"] for r in wrong],
        "usage": runner.usage,
        "results": results,
    }
    dest = ROOT / args.out
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k != "results"}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
