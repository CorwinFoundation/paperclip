"""BEAAA-41114: re-judge conversation attempts whose judges failed with HTTP 402.

On 2026-10-15 the OpenRouter account ran out of credit mid-run. Every judge
call then returned ``HTTP 402 Payment Required`` and the runner recorded the
attempt as ``could_not_run`` (fail-closed). The target reply and the complete
judge prompt are retained on each row, so the attempt can be graded again
without calling the target.

For each row whose judges include a 402 error, this script re-sends the
retained prompt of every 402 judge to the same pinned judge model. Judges that
had already answered are kept unchanged. It then resolves the two-judge panel
with the runner's own rule and appends a new row for the same (case, seed),
marked ``rejudged``. Original rows are never edited. The report aggregator lets
a later decided attempt replace an earlier ``could_not_run``. It is safe to
re-run: rows that already have a re-judged successor are skipped.

A new 402 stops the run before anything is written for that row.

    python3 scripts/beaaa41114_rejudge_402.py \\
        --rows artifacts/BEAAA-41114/full-run/vapi_09aea361/oss/oss_conv_live_vapi_09aea361_rows.jsonl \\
        --cap-usd 4
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.beaaa38922_conv_live_run import Meter, resolve_panel  # noqa: E402
from tools import dual_judge_grader as djg  # noqa: E402


class CreditExhausted(RuntimeError):
    pass


def is_402(judge: dict[str, Any]) -> bool:
    return "402" in str(judge.get("error") or "")


def needs_rejudge(row: dict[str, Any]) -> bool:
    return any(is_402(j) for j in row.get("judges") or [])


def rejudge_one(meter: Meter, key: str, judge: dict[str, Any]) -> dict[str, Any]:
    """Re-send one retained judge prompt; fail closed exactly as the runner does."""
    if not is_402(judge):
        return judge
    prompt = judge.get("prompt")
    if not prompt:
        raise ValueError("402 judge record has no retained prompt; cannot re-judge")
    record: dict[str, Any] = {"model": judge["model"], "prompt": prompt, "raw_output": None,
                              "served_model": None, "cost_usd": 0.0, "error": None, "rejudged": True}
    try:
        resp = meter.chat_full(key, [{"role": "user", "content": prompt}], judge["model"], None,
                               max_tokens=djg.MAX_JUDGE_TOKENS)
        record.update(raw_output=resp["content"], served_model=resp["served_model"],
                      cost_usd=resp["cost_usd"])
        parsed = djg._parse_provider_json(resp["content"])
        record.update(verdict=parsed["verdict"], reason=parsed["reason"])
    except Exception as exc:  # noqa: BLE001 - fail closed, retained as evidence
        if "402" in repr(exc):
            raise CreditExhausted(repr(exc)) from exc
        record.update(verdict="could-not-run", reason="judge_error", error=repr(exc))
    if record.get("verdict") not in ("pass", "fail"):
        record["verdict"] = "could-not-run"
    return record


def rejudged_row(row: dict[str, Any], judges: list[dict[str, Any]], at: str) -> dict[str, Any]:
    resolved = resolve_panel([j["verdict"] for j in judges])
    old = str(row.get("detail") or "")
    marker = "two_judge_pair:" if "two_judge_pair:" in old else "two_judge:"
    prefix = old.split(marker)[0] if marker in old else ""
    detail = prefix + marker + resolved + ":" + "|".join(j["verdict"] for j in judges)
    out = copy.deepcopy(row)
    out.update(judges=judges, detail=detail, verdict=resolved,
               **{"pass": {"pass": True, "fail": False}.get(resolved)},
               rejudged={"reason": "judge_http_402", "at_utc": at,
                         "judges_rejudged": sum(1 for j in row["judges"] if is_402(j))})
    return out


def pending(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    done = {(r.get("case_id"), r.get("seed")) for r in rows if r.get("rejudged")}
    return [r for r in rows if not r.get("rejudged") and needs_rejudge(r)
            and (r.get("case_id"), r.get("seed")) not in done]


def run(rows_path: Path, meter: Meter, key: str, workers: int, limit: int = 0) -> dict[str, Any]:
    rows = [json.loads(line) for line in rows_path.read_text().splitlines() if line.strip()]
    todo = pending(rows)
    if limit:
        todo = todo[:limit]
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lock = threading.Lock()
    stop = threading.Event()
    written = 0
    counts: dict[str, int] = {}

    def work(row: dict[str, Any]) -> None:
        nonlocal written
        if stop.is_set() or meter.over_cap():
            return
        try:
            judges = [rejudge_one(meter, key, j) for j in row["judges"]]
        except CreditExhausted:
            stop.set()
            return
        new = rejudged_row(row, judges, at)
        with lock, rows_path.open("a") as fh:
            fh.write(json.dumps(new) + "\n")
            written += 1
            counts[new["verdict"]] = counts.get(new["verdict"], 0) + 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, todo))
    return {"pending": len(todo), "written": written, "verdicts": counts,
            "spend_usd": round(meter.usd, 4), "stopped_on_402": stop.is_set(),
            "stopped_on_cap": meter.over_cap()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True, type=Path)
    ap.add_argument("--cap-usd", type=float, required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="re-judge only the first N rows (smoke)")
    ap.add_argument("--dry-run", action="store_true", help="count pending rows; no calls")
    args = ap.parse_args(argv)
    if args.dry_run:
        rows = [json.loads(x) for x in args.rows.read_text().splitlines() if x.strip()]
        print(json.dumps({"pending": len(pending(rows))}))
        return 0
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY not set")
    summary = run(args.rows, Meter(djg.JUDGE_A_MODEL, args.cap_usd), key, args.workers, args.limit)
    print(json.dumps(summary, indent=1))
    return 1 if summary["stopped_on_402"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
