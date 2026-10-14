#!/usr/bin/env python3
"""Classify changed paths into independently deployable solution lanes.

Unknown input and CI-policy changes fail closed by activating every lane.
"""
from __future__ import annotations

import argparse
import json
from pathlib import PurePosixPath

INSTINCT_PREFIXES = ("stage_b_pilot/", "ops/stage-b-inbound/")
INSTINCT_TEST_PREFIXES = (
    "tests/test_instinct",
    "tests/test_stage_b",
    "tests/test_capture_instinct",
    "tests/test_beaaa3607",
    "tests/test_beaaa37906_",
    "tests/test_beaaa37907_",
    "tests/test_beaaa37962_",
    "tests/test_beaaa38060",
    "tests/test_beaaa38116",
    "tests/test_beaaa38120",
    "tests/test_beaaa38147",
    "tests/test_beaaa38279",
    "tests/test_beaaa38728",
    "tests/test_beaaa38775",
    "tests/test_beaaa38867",
)
SHARED_PREFIXES = ("locks/", "requirements", ".github/workflows/", "scripts/ci_")
NON_PRODUCT_PREFIXES = ("docs/", "artifacts/", "decisions/")


def classify(paths: list[str] | None) -> dict[str, bool | str]:
    if not paths:
        return {"scanner": True, "instinct": True, "reason": "changed paths unavailable"}
    normalized = [PurePosixPath(p).as_posix().removeprefix("./") for p in paths]
    if any(not p or p.startswith("../") for p in normalized):
        return {"scanner": True, "instinct": True, "reason": "invalid changed path"}
    shared = any(p.startswith(SHARED_PREFIXES) for p in normalized)
    instinct_paths = INSTINCT_PREFIXES + INSTINCT_TEST_PREFIXES
    instinct = shared or any(p.startswith(instinct_paths) for p in normalized)
    scanner = shared or any(
        not p.startswith(instinct_paths + NON_PRODUCT_PREFIXES) for p in normalized
    )
    return {"scanner": scanner, "instinct": instinct, "reason": "classified changed paths"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--changed-json", default="-")
    parser.add_argument("--github-output")
    args = parser.parse_args()
    try:
        raw = input() if args.changed_json == "-" else args.changed_json
        paths = json.loads(raw)
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            paths = None
    except (EOFError, json.JSONDecodeError):
        paths = None
    result = classify(paths)
    lines = [f"scanner={str(result['scanner']).lower()}",
             f"instinct={str(result['instinct']).lower()}",
             f"reason={result['reason']}"]
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
