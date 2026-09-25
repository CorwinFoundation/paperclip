#!/usr/bin/env python3
"""Partition the affected-test selection into balanced pytest shards.

Why this exists (BEAAA-35635): `python pytest` was one 14-24 minute job, so a
failure email said only "python pytest failed" and cost 24 minutes to learn it.
The workflow now runs a matrix of shards, each its own check
(`python pytest (3/6)`), with a fan-in job keeping the required context name
`python pytest` unchanged.

Three modes:

  # 1. what does shard 3 of 6 run?  (reads the selector's expanded target list)
  scripts/ci_select_tests.py --expand | scripts/ci_shard_tests.py --shard 3 --of 6

  # 2. turn one shard's junit-xml into a per-file duration map, printed between
  #    markers so it can be recovered from a run log without an artifact upload
  scripts/ci_shard_tests.py --from-junit report.xml --emit-marker

  # 3. refresh the committed map from a whole run's logs (nightly full run)
  gh run view <run-id> --log > run.log
  scripts/ci_shard_tests.py --from-log run.log --out ci/test-durations.json

Balancing is longest-processing-time-first greedy bin packing over whole
targets: a target is never split, so pytest-xdist's --dist loadfile guarantee
(module-level fixtures and monkeypatched state never straddle workers) holds
across shards too. Unknown targets get the median known weight, so a brand new
test file lands in a plausible bin instead of being treated as free.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics
import sys
import xml.etree.ElementTree as ET

DURATIONS_PATH = pathlib.Path("ci/test-durations.json")
MARKER_BEGIN = "### CI_TEST_DURATIONS_JSON_BEGIN"
MARKER_END = "### CI_TEST_DURATIONS_JSON_END"
# GitHub prefixes every log line with "<job name>\t<timestamp> ".
LOG_PREFIX = re.compile(r"^.*?\d{4}-\d{2}-\d{2}T[\d:.]+Z\s?")


def load_durations(path: pathlib.Path) -> dict[str, float]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    weights = data.get("weights", data)
    return {str(k): float(v) for k, v in weights.items() if not str(k).startswith("__")}


# pytest's default discovery patterns (no python_files override in this repo).
TEST_FILE_GLOBS = ("test_*.py", "*_test.py")


def expand_targets(targets: list[str]) -> list[str]:
    """Turn a directory target into the test files inside it.

    The selector answers ALL with the single target ``tests`` (a directory).
    A directory cannot be partitioned, so expand it here with pytest's own
    default discovery patterns; node ids and file paths pass through.
    """
    out: list[str] = []
    for target in targets:
        path = pathlib.Path(target.split("::", 1)[0])
        if "::" not in target and path.is_dir():
            found = {str(f) for g in TEST_FILE_GLOBS for f in path.rglob(g)}
            if found:
                out.extend(sorted(found))
                continue
        out.append(target)
    # A directory target and one of its files can both be selected; dedupe.
    seen: set[str] = set()
    return [t for t in out if not (t in seen or seen.add(t))]


def drop_ignored(targets: list[str], ignores: list[str]) -> list[str]:
    """Remove targets that live under an --ignore path.

    pytest's own --ignore only filters *discovery*; a file named explicitly on
    the command line runs regardless. Expanding the selector's ``tests``
    directory turns every ignored file into an explicit argument, so the
    exclusion has to happen here instead (BEAAA-35635). The workflow passes the
    same list it passes to pytest.
    """
    if not ignores:
        return targets
    roots = [pathlib.PurePosixPath(i.rstrip("/")) for i in ignores]
    kept: list[str] = []
    for target in targets:
        path = pathlib.PurePosixPath(target_file(target))
        if any(path == root or root in path.parents for root in roots):
            continue
        kept.append(target)
    return kept

def target_file(target: str) -> str:
    """tests/x.py::Klass::test_y -> tests/x.py (weights are per file)."""
    return target.split("::", 1)[0]


def partition(targets: list[str], shards: int, weights: dict[str, float]) -> list[list[str]]:
    known = [w for w in weights.values() if w > 0]
    default = statistics.median(known) if known else 1.0
    # Deterministic: heaviest first, path as the tie-break, least-loaded bin
    # (lowest index on a tie) takes the next target.
    ordered = sorted(targets, key=lambda t: (-weights.get(target_file(t), default), t))
    bins: list[list[str]] = [[] for _ in range(shards)]
    load = [0.0] * shards
    for target in ordered:
        i = min(range(shards), key=lambda j: (load[j], j))
        bins[i].append(target)
        load[i] += weights.get(target_file(target), default)
    # Stable within a shard so the pytest command line does not churn run to run.
    return [sorted(b) for b in bins]


def durations_from_junit(paths: list[pathlib.Path]) -> dict[str, float]:
    weights: dict[str, float] = {}
    for path in paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            # pytest sets file= on every testcase; classname is a fallback.
            name = case.get("file") or (case.get("classname") or "").replace(".", "/") + ".py"
            if not name:
                continue
            weights[name] = round(weights.get(name, 0.0) + float(case.get("time") or 0.0), 3)
    return weights


def durations_from_log(path: pathlib.Path) -> dict[str, float]:
    merged: dict[str, float] = {}
    blob: list[str] = []
    capturing = False
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = LOG_PREFIX.sub("", raw)
        if line.strip() == MARKER_BEGIN:
            capturing, blob = True, []
            continue
        if line.strip() == MARKER_END:
            capturing = False
            for name, value in json.loads("\n".join(blob)).items():
                merged[name] = round(max(merged.get(name, 0.0), float(value)), 3)
            continue
        if capturing:
            blob.append(line)
    if not merged:
        raise SystemExit(f"{path}: no {MARKER_BEGIN} block found")
    return merged


def write_map(path: pathlib.Path, weights: dict[str, float]) -> None:
    payload = {
        "__comment__": (
            "Per-file pytest wall-clock seconds, used only to balance the "
            "python pytest shards (see scripts/ci_shard_tests.py). Refresh from "
            "a full run: gh run view <id> --log > run.log && python "
            "scripts/ci_shard_tests.py --from-log run.log --out ci/test-durations.json"
        ),
        "weights": dict(sorted(weights.items())),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shard", type=int, help="1-based shard index to print targets for")
    ap.add_argument("--of", type=int, help="total shard count")
    ap.add_argument("--targets-from", default="-", help="file with one target per line (default stdin)")
    ap.add_argument("--durations", type=pathlib.Path, default=DURATIONS_PATH)
    ap.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="PATH",
        help="drop targets under PATH; pass the same paths the pytest step ignores",
    )
    ap.add_argument("--from-junit", nargs="+", type=pathlib.Path)
    ap.add_argument("--from-log", type=pathlib.Path)
    ap.add_argument("--emit-marker", action="store_true", help="wrap JSON output in log markers")
    ap.add_argument("--out", type=pathlib.Path)
    args = ap.parse_args(argv)

    if args.from_junit or args.from_log:
        if args.from_log:
            weights = durations_from_log(args.from_log)
        else:
            weights = durations_from_junit([p for p in args.from_junit if p.exists()])
        if args.out:
            write_map(args.out, weights)
            print(f"wrote {args.out} ({len(weights)} files)", file=sys.stderr)
            return 0
        body = json.dumps(weights, indent=2, sort_keys=True)
        print(f"{MARKER_BEGIN}\n{body}\n{MARKER_END}" if args.emit_marker else body)
        return 0

    if not args.shard or not args.of:
        ap.error("--shard and --of are required unless --from-junit/--from-log is given")
    if not 1 <= args.shard <= args.of:
        ap.error(f"--shard must be in 1..{args.of}, got {args.shard}")

    stream = sys.stdin if args.targets_from == "-" else open(args.targets_from, encoding="utf-8")
    with stream as fh:
        targets = [line.strip() for line in fh if line.strip()]

    runnable = drop_ignored(expand_targets(targets), args.ignore)
    bins = partition(runnable, args.of, load_durations(args.durations))
    for target in bins[args.shard - 1]:
        print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
