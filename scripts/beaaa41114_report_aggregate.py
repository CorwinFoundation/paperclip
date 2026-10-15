#!/usr/bin/env python3
"""BEAAA-41114: per-unique-test aggregation for the Scanner Quality Report.

Pure, offline helper. It never calls a model and never changes scoring.

Two count gaps in the 9 Oct OSS run (BEAAA-39600) came from aggregating
retained attempt rows the wrong way:

* MultiJail 304/6 vs 305/5. ``scripts/run_full_scan_pack.py`` appends every
  attempt to ``<out>/details.jsonl`` with no job id, and the same ``--out``
  directory was reused by four different jobs. "Latest record per case" over
  that file mixed jobs and took MJL-006 from an abandoned 7-row job (pass)
  instead of the run-of-record job 581df2a6 (fail).
* JailbreakBench 197 vs 130. The JBB job was never finalised, so it has no
  ``summary.json``. The report copied "130/130" from a mid-run comment, which
  counted evidence rows, not the 197 registered cases. The job later reached
  155 rows; 42 cases were never attempted and disappeared from the
  denominator.

:func:`aggregate_unique_tests` fixes both: it refuses to mix jobs unless the
run of record is named, counts every expected case exactly once (never-run
cases are reported as ``not_run``, not dropped), lets a later decided attempt
replace an earlier ``could_not_run`` for the same (test, seed), and never lets
a ``could_not_run`` replace a decided verdict.

:func:`attribute_legacy_details` recovers the job id of each legacy
``details.jsonl`` line by matching (case, provider-reported cost) against the
job rows persisted in ``jobs.sqlite3``. It is only needed for pre-BEAAA-41114
artifacts.

CLI::

    python3 scripts/beaaa41114_report_aggregate.py reconcile \\
        --runs-dir <workspace>/tools/oss-import-runs/2026-10-08 \\
        --pack full-scan-oss-multijail@1.0 --subdir multijail --job 581df2a6
"""
from __future__ import annotations

import argparse
import ast
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

CASE_KEYS = ("stable_id", "uniqueness_key", "case_id", "probe_id")
JOB_KEYS = ("job_id", "run_id")
DECIDED = ("pass", "fail", "unresolved", "not_applicable")
UNDECIDED = "could_not_run"
_NORMAL = {
    "pass": "pass", "passed": "pass",
    "fail": "fail", "failed": "fail",
    "unresolved": "unresolved",
    "not_applicable": "not_applicable",
    "could_not_run": UNDECIDED,
}
OUTCOMES = ("pass", "fail", "flaky", "unresolved", "could_not_run", "not_applicable", "not_run")


class MixedRunError(ValueError):
    """Rows from more than one job were passed without naming the run of record."""


def _first(row: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def normalise_verdict(row: Mapping[str, Any]) -> str:
    """Map runner verdict spellings onto one vocabulary.

    A row with no verdict (e.g. a ``could_not_run`` evidence row whose
    ``verdict`` is null) is undecided.
    """
    raw = row.get("verdict")
    if raw is None:
        status = row.get("status")
        return UNDECIDED if status in (None, "could_not_run", "incomplete") else _NORMAL.get(status, UNDECIDED)
    try:
        return _NORMAL[str(raw)]
    except KeyError as exc:
        raise ValueError(f"unknown verdict {raw!r}") from exc


def _job_matches(job: Any, run_of_record: str) -> bool:
    return job is not None and str(job).startswith(run_of_record)


def resolve_attempts(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str, int], str]:
    """Collapse attempts to one verdict per (test, seed), in file order.

    * a decided verdict (pass/fail/unresolved/not_applicable) replaces an
      earlier ``could_not_run`` for the same (test, seed);
    * a ``could_not_run`` never replaces a decided verdict;
    * among decided attempts the last one written wins, which is what
      ``FullScanJobStore.checkpoint`` persists (merge keyed on (probe_id, seed)).
    """
    resolved: dict[tuple[str, int], str] = {}
    for row in rows:
        case = _first(row, CASE_KEYS)
        if case is None:
            raise ValueError(f"row has no test identity: {sorted(row)}")
        key = (str(case), int(row.get("seed") or 0))
        verdict = normalise_verdict(row)
        if verdict == UNDECIDED and resolved.get(key, UNDECIDED) != UNDECIDED:
            continue
        resolved[key] = verdict
    return resolved


def _case_outcome(verdicts: list[str], missing_seeds: bool) -> str:
    if missing_seeds or UNDECIDED in verdicts:
        return "could_not_run"
    if "unresolved" in verdicts:
        return "unresolved"
    if "not_applicable" in verdicts:
        if all(v == "not_applicable" for v in verdicts):
            return "not_applicable"
        raise ValueError(f"case mixes not_applicable with judged seeds: {verdicts}")
    if all(v == "pass" for v in verdicts):
        return "pass"
    if all(v == "fail" for v in verdicts):
        return "fail"
    return "flaky"


def aggregate_unique_tests(
    rows: Iterable[Mapping[str, Any]],
    *,
    expected_case_ids: Iterable[str] | None = None,
    seeds: Sequence[int] | None = None,
    run_of_record: str | None = None,
) -> dict[str, Any]:
    """Per-unique-test outcome counts from retained per-attempt rows.

    ``rows`` are dicts with a test identity (``stable_id``/``uniqueness_key``/
    ``case_id``/``probe_id``), an optional ``seed`` (default 0), a ``verdict``
    and an optional ``job_id``/``run_id``. File order is attempt order.

    ``run_of_record`` (a job id or unique prefix) keeps only that job's rows.
    Without it, rows carrying more than one job id raise :class:`MixedRunError`.

    ``expected_case_ids`` is the registered pack; a registered case with no
    row counts once as ``not_run``. A row for an unregistered case raises.

    ``seeds`` is the K seed set; a case missing any seed is ``could_not_run``.
    """
    rows = list(rows)
    jobs = {_first(r, JOB_KEYS) for r in rows} - {None}
    if run_of_record is not None:
        kept = [r for r in rows if _job_matches(_first(r, JOB_KEYS), run_of_record)]
        if rows and not kept:
            raise ValueError(f"no rows belong to run of record {run_of_record!r}")
    elif len(jobs) > 1:
        raise MixedRunError(
            f"rows come from {len(jobs)} jobs {sorted(map(str, jobs))}; name the run of record"
        )
    else:
        kept = rows
    resolved = resolve_attempts(kept)

    by_case: dict[str, dict[int, str]] = defaultdict(dict)
    for (case, seed), verdict in resolved.items():
        by_case[case][seed] = verdict

    expected = list(dict.fromkeys(expected_case_ids)) if expected_case_ids is not None else None
    if expected is not None:
        unknown = sorted(set(by_case) - set(expected))
        if unknown:
            raise ValueError(f"rows for unregistered cases: {unknown[:5]}")
        universe = expected
    else:
        universe = sorted(by_case)

    per_case: dict[str, str] = {}
    for case in universe:
        if case not in by_case:
            per_case[case] = "not_run"
            continue
        seen = by_case[case]
        missing = bool(seeds) and bool(set(seeds) - set(seen))
        per_case[case] = _case_outcome([seen[s] for s in sorted(seen)], missing)

    counts = Counter(per_case.values())
    return {
        "unique_tests": len(per_case),
        "measured": sum(counts[o] for o in ("pass", "fail", "flaky", "unresolved")),
        "counts": {o: counts.get(o, 0) for o in OUTCOMES},
        "attempt_rows_in": len(rows),
        "attempt_rows_used": len(kept),
        "rows_excluded_other_jobs": len(rows) - len(kept),
        "jobs_seen": sorted(map(str, jobs)),
        "run_of_record": run_of_record,
        "per_case": per_case,
    }


# ---------------------------------------------------------------------------
# Legacy artifacts (scripts/run_full_scan_pack.py, 8-9 Oct 2026)
# ---------------------------------------------------------------------------

def load_jobs(db_path: str | Path, pack_id: str) -> dict[str, dict[str, Any]]:
    """Read-only load of every job for ``pack_id`` from a FullScanJobStore DB."""
    uri = f"file:{Path(db_path)}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        jobs = {}
        for job_id, payload in conn.execute("select job_id, job_json from full_scan_jobs"):
            job = json.loads(payload)
            if job.get("pack_id") == pack_id:
                jobs[job_id] = job
    return jobs


def _row_cost(row: Mapping[str, Any]) -> tuple[Any, Any]:
    cost = row.get("cost") or {}
    if isinstance(cost, str):
        cost = ast.literal_eval(cost)
    return cost.get("target_cost_usd"), cost.get("judge_cost_usd")


def attribute_legacy_details(
    details: Sequence[Mapping[str, Any]],
    jobs: Mapping[str, Mapping[str, Any]],
    case_order: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Attach ``job_id`` to legacy ``details.jsonl`` lines.

    Each line is matched on (case_id, target cost, judge cost) against the
    evidence rows each job persisted. Lines are split into invocation segments
    wherever the case stops advancing in ``case_order`` (the pack's registry
    order, which every runner walks; defaults to the first job's
    ``registry_cases``). A uniquely matched line keeps its job. A line matching
    several jobs, or none (an attempt later overwritten in the DB, e.g. a
    could_not_run that a resume replaced), takes the job of the nearest
    attributed line in its segment (the previous one, else the next one) that
    is consistent with its candidates.
    """
    index: dict[tuple[str, Any, Any], set[str]] = defaultdict(set)
    for job_id, job in jobs.items():
        for row in job.get("evidence_rows", []):
            index[(row["probe_id"], *_row_cost(row))].add(job_id)

    candidates = [
        index.get((d["case_id"], d.get("target_cost_usd"), d.get("judge_cost_usd")), set())
        for d in details
    ]
    if case_order is None:
        case_order = [c["case_id"] for c in next(iter(jobs.values()))["registry_cases"]]
    position = {case: n for n, case in enumerate(case_order)}
    segments: list[list[int]] = []
    for i, d in enumerate(details):
        if not segments or position[d["case_id"]] <= position[details[segments[-1][-1]]["case_id"]]:
            segments.append([])
        segments[-1].append(i)

    assigned: dict[int, str] = {i: next(iter(c)) for i, c in enumerate(candidates) if len(c) == 1}
    for seg in segments:
        for i in seg:
            if i in assigned:
                continue
            cands = candidates[i]
            # Nearest attributed line in the same segment, earlier lines first.
            before = [assigned[j] for j in reversed(seg) if j < i and j in assigned]
            after = [assigned[j] for j in seg if j > i and j in assigned]
            for job in before[:1] + after[:1]:
                if not cands or job in cands:
                    assigned[i] = job
                    break
            else:
                raise ValueError(f"cannot attribute details line {i} ({details[i]['case_id']})")
    return [{**d, "job_id": assigned[i], "seed": 0, "line": i} for i, d in enumerate(details)]


def committed_verdicts(job: Mapping[str, Any]) -> dict[str, str]:
    """The per-case verdicts a job persisted (what rows.json/summary.json report)."""
    return {row["probe_id"]: normalise_verdict(row) for row in job.get("evidence_rows", [])}


def _latest_per_case(details: Iterable[Mapping[str, Any]]) -> Counter:
    """The BEAAA-39600 report's dedup: last details line per case, any job."""
    last: dict[str, str] = {}
    for d in details:
        last[d["case_id"]] = normalise_verdict(d)
    return Counter(last.values())


def reconcile_legacy_pack(runs_dir: Path, pack_id: str, subdir: str, job: str) -> dict[str, Any]:
    jobs = load_jobs(runs_dir / "jobs.sqlite3", pack_id)
    matches = [j for j in jobs if j.startswith(job)]
    if len(matches) != 1:
        raise ValueError(f"run of record {job!r} matches {matches}")
    record = jobs[matches[0]]
    details = [json.loads(line) for line in (runs_dir / subdir / "details.jsonl").open(encoding="utf-8") if line.strip()]
    expected = [c["case_id"] for c in record["registry_cases"]]
    attributed = attribute_legacy_details(details, jobs, expected)
    result = aggregate_unique_tests(attributed, expected_case_ids=expected, run_of_record=matches[0])
    result["latest_line_per_case_any_job"] = dict(_latest_per_case(details))
    result["committed_rows"] = dict(Counter(committed_verdicts(record).values()))
    result["job_status"] = record["status"]
    result["details_lines_by_job"] = dict(Counter(a["job_id"][:8] for a in attributed))
    result.pop("per_case")
    return result


def legacy_fixture(runs_dir: Path, pack_id: str, subdir: str, job: str) -> dict[str, Any]:
    """Minimal, model-free extract of one legacy pack for offline tests.

    Keeps only identity, verdict and attributed job per details line, the
    registered case order, and the run-of-record job's committed verdicts.
    No prompts or replies are copied.
    """
    jobs = load_jobs(runs_dir / "jobs.sqlite3", pack_id)
    (record_id,) = [j for j in jobs if j.startswith(job)]
    record = jobs[record_id]
    details = [json.loads(line) for line in (runs_dir / subdir / "details.jsonl").open(encoding="utf-8") if line.strip()]
    expected = [c["case_id"] for c in record["registry_cases"]]
    attributed = attribute_legacy_details(details, jobs, expected)
    return {
        "pack_id": pack_id,
        "source": f"{runs_dir.name}/{subdir}/details.jsonl + jobs.sqlite3 (BEAAA-39600)",
        "run_of_record": record_id,
        "run_of_record_status": record["status"],
        "registered_cases": expected,
        "committed_verdicts": committed_verdicts(record),
        "details": [[a["line"], a["case_id"], normalise_verdict(a), a["job_id"]] for a in attributed],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, help_text in (
        ("reconcile", "reconcile one legacy run_full_scan_pack.py pack"),
        ("fixture", "print the minimal offline test fixture for one pack"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--runs-dir", required=True, type=Path)
        cmd.add_argument("--pack", required=True)
        cmd.add_argument("--subdir", required=True)
        cmd.add_argument("--job", required=True, help="run-of-record job id or unique prefix")
    args = parser.parse_args(argv)
    fn = reconcile_legacy_pack if args.cmd == "reconcile" else legacy_fixture
    print(json.dumps(fn(args.runs_dir, args.pack, args.subdir, args.job),
                     indent=None if args.cmd == "fixture" else 2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
