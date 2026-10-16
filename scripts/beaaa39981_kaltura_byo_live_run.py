"""Run the accepted BEAAA-39981 Kaltura BYO-policy v3 corpus at K=3.

Report-only. Results do not feed Starter/Full Scan scores or underwriting.
The only network traffic is to the pinned OpenRouter model and judge.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanners.starter_judge import JUDGE_MODEL
from scripts.beaaa38961_full_scan_e_live_run import OPENROUTER_URL, Meter, _append_jsonl
from scripts.beaaa39970_full_scan_pressure_live_run import _judge


class AuthorizationStop(RuntimeError):
    pass


def _post(meter: Meter, key: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = meter.poster(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=90.0,
        session_id="beaaa-39981-kaltura-v3",
    )
    if response.status_code in {402, 403}:
        raise AuthorizationStop(f"openrouter_http_{response.status_code}: {response.text[:300]}")
    if response.status_code >= 400:
        raise RuntimeError(f"openrouter_http_{response.status_code}: {response.text[:300]}")
    return response.json()


def _run_one(row: dict[str, Any], seed: int, *, key: str, model: str, meter: Meter) -> dict[str, Any]:
    messages: list[dict[str, str]] = [{"role": "system", "content": str(row["system"])}]
    transcript: list[dict[str, str]] = []
    final_reply = ""
    for user_text in row["turns"]:
        messages.append({"role": "user", "content": str(user_text)})
        body = _post(meter, key, {"model": model, "messages": messages, "temperature": 0, "seed": seed})
        choices = body.get("choices") or []
        if not choices:
            raise RuntimeError("openrouter_empty_choices")
        final_reply = str((choices[0].get("message") or {}).get("content") or "")
        messages.append({"role": "assistant", "content": final_reply})
        transcript.append({"user": str(user_text), "assistant": final_reply})
    violated = _judge(meter, key, JUDGE_MODEL, str(row["pass_rule"]), str(row["turns"][-1]), final_reply)
    return {
        "case_id": row["id"], "clause_id": row["clause_id"], "clause_kind": row["clause_kind"],
        "framing": row["framing"], "seed": seed, "verdict": "fail" if violated else "pass",
        "pass_rule": row["pass_rule"], "reference_answer": row["reference_answer"],
        "policy_sha256": row["policy_sha256"], "content_sha256": row["content_sha256"],
        "transcript": transcript,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="openai/gpt-4.1-mini")
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--cap-usd", type=float, default=15.0)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--corpus", type=Path, default=Path("artifacts/BEAAA-39981/generated/kaltura-public-policy.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("artifacts/BEAAA-39981/live-v3"))
    args = parser.parse_args()
    if args.model != "openai/gpt-4.1-mini" or args.k != 3 or args.cap_usd > 15:
        raise SystemExit("authorization requires model=openai/gpt-4.1-mini, k=3, cap-usd<=15")
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY not set")

    rows = [json.loads(line) for line in args.corpus.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) != 190 or len({row["id"] for row in rows}) != 190:
        raise SystemExit("accepted v3 corpus must contain exactly 190 unique cases")
    if args.limit:
        rows = rows[:args.limit]
    args.out.mkdir(parents=True, exist_ok=True)
    details = args.out / "kaltura_v3_details.jsonl"
    results: list[dict[str, Any]] = []
    done: set[tuple[str, int]] = set()
    prior_spend = {"usd": 0.0, "model_calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
    if args.resume and details.exists():
        for line in details.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            results.append(item)
            done.add((str(item["case_id"]), int(item["seed"])))
        summary_path = args.out / "kaltura_v3_summary.json"
        if summary_path.exists():
            saved = json.loads(summary_path.read_text()).get("spend", {})
            for field in prior_spend:
                prior_spend[field] = saved.get(field, prior_spend[field])
    else:
        details.write_text("", encoding="utf-8")

    meter = Meter(args.model, args.cap_usd)
    meter.usd = float(prior_spend["usd"])
    meter.calls = int(prior_spend["model_calls"])
    meter.prompt_tokens = int(prior_spend["prompt_tokens"])
    meter.completion_tokens = int(prior_spend["completion_tokens"])
    errors: list[dict[str, Any]] = []
    stop = threading.Event()
    lock = threading.Lock()
    started = time.monotonic()
    jobs = [(row, seed) for row in rows for seed in range(args.k) if (str(row["id"]), seed) not in done]

    def run(job: tuple[dict[str, Any], int]) -> None:
        row, seed = job
        if stop.is_set() or meter.usd >= args.cap_usd - 0.50:
            stop.set()
            return
        try:
            result = _run_one(row, seed, key=key, model=args.model, meter=meter)
        except AuthorizationStop as exc:
            stop.set()
            with lock:
                errors.append({"case_id": row["id"], "seed": seed, "outcome": "authorization_stop", "error": str(exc)})
            return
        except Exception as exc:
            with lock:
                errors.append({"case_id": row["id"], "seed": seed, "outcome": "error", "error": repr(exc)[:500]})
            return
        with lock:
            results.append(result)
        _append_jsonl(details, result, lock)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(run, jobs))

    by_case: dict[str, list[str]] = defaultdict(list)
    for result in results:
        by_case[str(result["case_id"])].append(str(result["verdict"]))
    complete = {case_id: verdicts for case_id, verdicts in by_case.items() if len(verdicts) == args.k}
    summary = {
        "issue": "BEAAA-39981", "disposition": "report-only", "model": args.model,
        "judge_model": JUDGE_MODEL, "k": args.k, "unique_cases_admitted": len(rows),
        "unique_cases_complete": len(complete), "attempts_complete": len(results),
        "attempts_expected": len(rows) * args.k, "pass_all_k": sum(all(v == "pass" for v in vs) for vs in complete.values()),
        "flaky": sum("pass" in vs and "fail" in vs for vs in complete.values()),
        "verdicts": dict(Counter(r["verdict"] for r in results)), "errors": errors,
        "stopped": stop.is_set(),
        "spend": {"usd": round(meter.usd, 6), "cap_usd": args.cap_usd, "model_calls": meter.calls,
                  "prompt_tokens": meter.prompt_tokens, "completion_tokens": meter.completion_tokens},
        "runtime_seconds": round(time.monotonic() - started, 3),
        "replay": {"command": shlex.join([sys.executable, *sys.argv]), "resume_supported": True},
        "boundaries": {"score_promotion": False, "underwriting_use": False, "customer_evidence": False,
                       "human_ground_truth": False},
    }
    (args.out / "kaltura_v3_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    samples = sorted(results, key=lambda item: (item["verdict"] != "fail", item["case_id"], item["seed"]))[:10]
    (args.out / "kaltura_v3_samples.json").write_text(
        json.dumps(samples, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        by_kind[str(result["clause_kind"])].append(result)
    table = ["| Clause kind | Attempts | Pass | Fail |", "|---|---:|---:|---:|"]
    for kind, items in sorted(by_kind.items()):
        table.append(f"| {kind} | {len(items)} | {sum(i['verdict'] == 'pass' for i in items)} | {sum(i['verdict'] == 'fail' for i in items)} |")
    (args.out / "kaltura_v3_table.md").write_text("\n".join(table) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if len(results) == len(rows) * args.k and not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
