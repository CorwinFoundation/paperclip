#!/usr/bin/env python3
"""Run BEAAA-40049 injection wave 2 at K=3 through OpenRouter."""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanners.full_scan_injection_wave2 import ALL_CASES, deterministic_verdict

URL = "https://openrouter.ai/api/v1/chat/completions"
PRICES = {"openai/gpt-4.1-mini": (0.40, 1.60)}


class Meter:
    def __init__(self, model: str, cap: float) -> None:
        self.model, self.cap, self.usd = model, cap, 0.0
        self.calls = self.prompt_tokens = self.completion_tokens = 0
        self.lock = threading.Lock()

    def over_cap(self) -> bool:
        with self.lock:
            return self.usd >= self.cap - 0.10

    def chat(self, key: str, prompt: str, seed: int) -> tuple[str, dict]:
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "seed": seed,
            "max_tokens": 128,
            "usage": {"include": True},
        }).encode()
        req = urllib.request.Request(URL, data=body, method="POST", headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as response:
            payload = json.loads(response.read())
        usage = payload.get("usage") or {}
        pt, ct = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
        cost = usage.get("cost")
        if cost is None:
            pin, pout = PRICES[self.model]
            cost = pt / 1e6 * pin + ct / 1e6 * pout
        with self.lock:
            self.calls += 1
            self.prompt_tokens += pt
            self.completion_tokens += ct
            self.usd += float(cost)
        return payload["choices"][0]["message"]["content"] or "", usage


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="openai/gpt-4.1-mini")
    ap.add_argument("--seeds", type=int, nargs="+", default=[42, 43, 44])
    ap.add_argument("--cap-usd", type=float, default=15.0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default="artifacts/beaaa-40049/live-k3")
    args = ap.parse_args()
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise SystemExit("OPENROUTER_API_KEY not set")
    if args.model not in PRICES:
        raise SystemExit(f"no pinned price for {args.model}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows_path = out / "rows.jsonl"
    done: set[tuple[str, int]] = set()
    rows: list[dict] = []
    if rows_path.exists():
        for line in rows_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            rows.append(row)
            done.add((row["case_id"], row["seed"]))

    meter, write_lock = Meter(args.model, args.cap_usd), threading.Lock()
    skipped: list[tuple[str, int]] = []

    def work(item) -> None:
        case, seed = item
        if (case.case_id, seed) in done:
            return
        if meter.over_cap():
            with write_lock:
                skipped.append((case.case_id, seed))
            return
        try:
            reply, usage = meter.chat(key, case.conversation, seed)
            verdict = deterministic_verdict(case, reply, accepts_images=True)
            detail = "canary_absent_and_end_state_present" if verdict == "pass" else "canary_or_end_state_failure"
        except Exception as exc:
            reply, usage, verdict = "", {}, "could_not_run"
            detail = f"transport_error:{type(exc).__name__}:{str(exc)[:120]}"
        row = {"case_id": case.case_id, "family": case.family, "carrier": case.carrier,
               "language": case.language, "seed": seed, "verdict": verdict, "detail": detail,
               "reply_excerpt": reply[:300], "model": args.model, "usage": usage}
        with write_lock:
            rows.append(row)
            with rows_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    pending = [(case, seed) for case in ALL_CASES for seed in args.seeds]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, pending))

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["case_id"]].append(row)
    case_rows = []
    for case_id, attempts in sorted(grouped.items()):
        attempts.sort(key=lambda r: r["seed"])
        verdicts = [r["verdict"] for r in attempts]
        verdict = ("could_not_run" if "could_not_run" in verdicts else
                   "pass" if len(verdicts) == len(args.seeds) and all(v == "pass" for v in verdicts) else
                   "fail" if len(verdicts) == len(args.seeds) and all(v == "fail" for v in verdicts) else "flaky")
        first = attempts[0]
        case_rows.append({"case_id": case_id, "family": first["family"], "carrier": first["carrier"],
                          "language": first["language"], "verdict": verdict, "raw_verdicts": verdicts})

    def table(key_name: str) -> dict:
        result = defaultdict(lambda: {"pass": 0, "fail": 0, "flaky": 0, "could_not_run": 0, "N/A": 0})
        for row in case_rows:
            result[row[key_name]][row["verdict"]] += 1
        return dict(sorted(result.items()))

    summary = {
        "issue": "BEAAA-40049", "model": args.model, "seeds": args.seeds,
        "catalogue_cases": len(ALL_CASES), "attempts_expected": len(ALL_CASES) * len(args.seeds),
        "attempts_recorded": len(rows), "cases_aggregated": len(case_rows),
        "skipped_cap": len(skipped), "per_carrier": table("carrier"), "per_language": table("language"),
        "spend": {"session_usd": round(meter.usd, 6), "cap_usd": args.cap_usd,
                  "calls": meter.calls, "prompt_tokens": meter.prompt_tokens,
                  "completion_tokens": meter.completion_tokens},
        "calibration_status": "uncalibrated",
    }
    (out / "case_rollup.json").write_text(json.dumps(case_rows, ensure_ascii=False, indent=2) + "\n")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    (out / "sample_rows.json").write_text(json.dumps(rows[:10], ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if len(rows) == len(ALL_CASES) * len(args.seeds) else 1


if __name__ == "__main__":
    raise SystemExit(main())
