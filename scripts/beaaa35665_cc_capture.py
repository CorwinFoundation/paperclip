"""BEAAA-35665 CAPTURE: measure the 100 ``confidence_calibration`` rows against the authorized target.

Budget, priced before any call went out:
  100 rows x 5 seeds = hard cap 500 target calls, 0 judge calls.
  The oracle is ``deterministic_verifier`` on every one of the 100 rows -- a
  case-insensitive accept/reject substring check from the row's own
  ``expected_observable_behavior``. No judge is involved, so no judge budget is
  touched. A row that errors STAYS errored and is reported with its literal
  reason; no retry, no top-up, no second pass.

The 100 rows are DERIVED from the BEAAA-18720 catalogue at the merged SHA, not
listed here: every ``confidence_calibration`` row in
``artifacts/beaaa-18720/unified-probe-inventory.jsonl``. All 100 declare
``deterministic_oracle.type == deterministic_verifier`` and pin the same
``source_revision``; the script asserts both rather than trusting them.

Correctness and confidence parsing are NOT reimplemented here. Both come from
``harness.bench.confidence_calibration``, the adapter that already measures
ARE dim P1.5 -- the same code path BEAAA-33020 measured this family with. That
is deliberate: the CEO asked for the reproduce-or-diverge check against 33020's
easy 45.0% / hard 26.7%, and a second implementation of the grader would make a
divergence unattributable.

Seeding: ``OpenRouterRunner`` takes no seed, so this module wraps the
OpenAI-compatible surface directly with ``seed=`` per call, the same way
``scripts/beaaa35020_tool_misuse_corrected_n5.py`` does. Temperature is 0.0.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.bench.confidence_calibration import (  # noqa: E402
    ELICITATION_SUFFIX,
    CalibrationItem,
    compute_brier,
    compute_ece,
    make_substring_correct_check,
    parse_confidence,
)
from harness.runner import ModelRunner  # noqa: E402

FAMILY = "confidence_calibration"
CATALOGUE = ROOT / "artifacts" / "beaaa-18720" / "unified-probe-inventory.jsonl"
EXPECTED_ROWS = 100
EXPECTED_ORACLE = "deterministic_verifier"
PINNED_SOURCE_REVISION = "6e78dae48131f590cdd59fada023089c53a45236"
DEFAULT_SEEDS = [0, 1, 2, 3, 4]
MAX_TARGET_CALLS = EXPECTED_ROWS * len(DEFAULT_SEEDS)

# BEAAA-33020 measured this same family on this same 100-row set. The CEO asked
# for an explicit reproduce-or-diverge line against these two numbers.
BEAAA33020_ACCURACY = {"easy": 0.450, "hard": 0.267}
# What counts as a divergence worth stopping activation for, in accuracy points.
DIVERGENCE_TOLERANCE = 0.10

# OpenRouter per-million-token prices for the pinned model, used only to turn
# the run's real token usage into the measured per-row cost the CEO asked for
# on the 228 pinned_llm_judge rows. Priced from the usage the API returns, not
# from an assumed token count.
PRICE_USD_PER_MTOK = {"openai/gpt-4.1-mini": {"prompt": 0.40, "completion": 1.60}}


class SeededOpenRouterRunner(ModelRunner):
    """OpenRouter chat-completions with an explicit per-request ``seed``.

    ``harness.runner.OpenRouterRunner`` exposes no seed parameter, and the N=5
    seed sweep is the whole point of the stability floor, so the seed cannot be
    left implicit. Everything else (temperature 0.0, the model id form) matches
    the standing dev-mode target.
    """

    def __init__(self, model: str, seed: int, *, max_tokens: int = 256) -> None:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise SystemExit("OPENROUTER_API_KEY not set; refusing to run with no target")
        from openai import OpenAI  # local import: egress only on a real run

        self._model = model
        self._seed = seed
        self._max_tokens = max_tokens
        self._client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def model_id(self) -> str:
        return f"openrouter:{self._model}"

    def complete(self, prompt: str) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            seed=self._seed,
            max_tokens=self._max_tokens,
        )
        usage = getattr(resp, "usage", None)
        if usage is not None:
            self.usage["prompt_tokens"] += int(getattr(usage, "prompt_tokens", 0) or 0)
            self.usage["completion_tokens"] += int(getattr(usage, "completion_tokens", 0) or 0)
        return str(resp.choices[0].message.content or "")


def map_rows() -> list[dict[str, Any]]:
    """MAP: the 100 catalogue rows, with the oracle each row declares.

    Fails closed on anything that would make the slice not-what-it-says: a row
    count other than 100, a non-``deterministic_verifier`` oracle, an unpinned
    source revision, or a row with no accept substrings (which would make every
    response silently incorrect).
    """
    rows = [
        json.loads(line)
        for line in CATALOGUE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    cc = [r for r in rows if r["family_id"] == FAMILY]
    if len(cc) != EXPECTED_ROWS:
        raise SystemExit(f"expected {EXPECTED_ROWS} {FAMILY} rows, catalogue has {len(cc)}")
    mapped = []
    for row in sorted(cc, key=lambda r: r["stable_case_id"]):
        case_id = str(row["stable_case_id"])
        oracle = row["deterministic_oracle"]
        if oracle.get("type") != EXPECTED_ORACLE:
            raise SystemExit(f"{case_id}: oracle is {oracle.get('type')!r}, not {EXPECTED_ORACLE!r}")
        revision = str(oracle.get("spec", {}).get("source_revision") or "")
        if revision != PINNED_SOURCE_REVISION:
            raise SystemExit(f"{case_id}: source_revision {revision!r} is not the pinned {PINNED_SOURCE_REVISION!r}")
        behavior = row["expected_observable_behavior"]
        accepts = list(behavior.get("accept_substrings") or [])
        if not accepts:
            raise SystemExit(f"{case_id}: no accept_substrings; every response would grade incorrect")
        turns = row["prompt_turn_sequence"]
        if len(turns) != 1 or not str(turns[0].get("prompt") or "").strip():
            raise SystemExit(f"{case_id}: expected exactly one non-empty prompt turn, got {turns!r}")
        mapped.append(
            {
                "stable_case_id": case_id,
                "dedupe_sha256": str(row["dedupe_sha256"]),
                "prompt": str(turns[0]["prompt"]),
                "accept_substrings": accepts,
                "reject_substrings": list(behavior.get("reject_substrings") or []),
                "difficulty": behavior.get("difficulty"),
                "category": behavior.get("category"),
                "source_revision": revision,
            }
        )
    return mapped


def _item(row: dict[str, Any]) -> CalibrationItem:
    return CalibrationItem(
        id=row["stable_case_id"],
        prompt=row["prompt"],
        correct_check=make_substring_correct_check(
            row["accept_substrings"], row["reject_substrings"]
        ),
        gold=", ".join(row["accept_substrings"]),
        difficulty=row["difficulty"],
        category=row["category"],
    )


def capture(rows: list[dict[str, Any]], *, seeds: list[int], model: str) -> dict[str, Any]:
    """CAPTURE: every row at every seed against the authorized target."""
    budget = len(rows) * len(seeds)
    if budget > MAX_TARGET_CALLS:
        raise SystemExit(f"{budget} calls exceeds the priced ceiling of {MAX_TARGET_CALLS}")
    items = {row["stable_case_id"]: _item(row) for row in rows}

    records: list[dict[str, Any]] = []
    spent = 0
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    for seed in seeds:
        subject = SeededOpenRouterRunner(model, seed)
        for row in rows:
            if spent >= MAX_TARGET_CALLS:
                raise SystemExit(f"ceiling reached at {spent}; {row['stable_case_id']} unmeasured")
            item = items[row["stable_case_id"]]
            prompt = item.prompt + ELICITATION_SUFFIX
            started = time.time()
            spent += 1
            try:
                response = subject.complete(prompt)
                error = None
            except Exception as exc:  # a row that errors stays errored
                response, error = "", f"{type(exc).__name__}: {exc}"
            records.append(
                {
                    "stable_case_id": row["stable_case_id"],
                    "dedupe_sha256": row["dedupe_sha256"],
                    "seed": seed,
                    "difficulty": row["difficulty"],
                    "category": row["category"],
                    "rendered_prompt": prompt,
                    "response": response,
                    "confidence": None if error else parse_confidence(response),
                    "correct": False if error else bool(item.correct_check(response)),
                    "error": error,
                    "latency_ms": int((time.time() - started) * 1000),
                    "model_id": subject.model_id(),
                }
            )
            if spent % 25 == 0:
                print(f"  [{spent:3d}/{budget}] seed={seed} {row['stable_case_id']}", flush=True)
        for key in usage:
            usage[key] += subject.usage[key]
    return {"records": records, "target_calls": spent, "usage": usage, "model": model, "seeds": seeds}


def _usd(usage: dict[str, int], model: str) -> float | None:
    price = PRICE_USD_PER_MTOK.get(model)
    if price is None:
        return None
    return round(
        usage["prompt_tokens"] / 1_000_000 * price["prompt"]
        + usage["completion_tokens"] / 1_000_000 * price["completion"],
        6,
    )


def validate(rows: list[dict[str, Any]], captured: dict[str, Any]) -> dict[str, Any]:
    """VALIDATE: per-row verdicts, seed stability, family ECE, and the 33020 cross-check."""
    by_case: dict[str, list[dict[str, Any]]] = {}
    for rec in captured["records"]:
        by_case.setdefault(rec["stable_case_id"], []).append(rec)

    per_row = []
    for row in rows:
        case_id = row["stable_case_id"]
        runs = sorted(by_case.get(case_id, []), key=lambda r: r["seed"])
        errored = [r for r in runs if r["error"]]
        corrects = [bool(r["correct"]) for r in runs if not r["error"]]
        n_pass = sum(corrects)
        n_seeds = len(corrects)
        per_row.append(
            {
                "stable_case_id": case_id,
                "dedupe_sha256": row["dedupe_sha256"],
                "difficulty": row["difficulty"],
                "category": row["category"],
                "n_seeds": n_seeds,
                "n_passed": n_pass,
                # Unanimity is the floor: a row that answers differently across
                # seeds is unmeasured, not a fractional score.
                "seed_stable": n_seeds > 0 and n_pass in (0, n_seeds),
                "verdict": (
                    "error" if errored and not corrects
                    else "pass" if n_seeds and n_pass == n_seeds
                    else "fail" if n_seeds and n_pass == 0
                    else "unstable"
                ),
                "errors": [r["error"] for r in errored],
                "confidences": [r["confidence"] for r in runs],
                "responses": {str(r["seed"]): r["response"] for r in runs},
            }
        )

    stable = [r for r in per_row if r["seed_stable"]]
    # Accuracy per difficulty stratum on stable rows only -- the 33020 comparison.
    strata: dict[str, dict[str, Any]] = {}
    for level in ("easy", "medium", "hard"):
        rows_at = [r for r in per_row if r["difficulty"] == level]
        graded = [r for r in rows_at if r["verdict"] in ("pass", "fail")]
        acc = (sum(1 for r in graded if r["verdict"] == "pass") / len(graded)) if graded else None
        entry: dict[str, Any] = {"n_rows": len(rows_at), "n_graded": len(graded), "accuracy": acc}
        prior = BEAAA33020_ACCURACY.get(level)
        if prior is not None and acc is not None:
            entry["beaaa33020_accuracy"] = prior
            entry["delta"] = round(acc - prior, 4)
            entry["reproduces"] = abs(acc - prior) <= DIVERGENCE_TOLERANCE
        strata[level] = entry

    # Family ECE per seed, from the same adapter code BEAAA-33020 used.
    per_seed_ece = {}
    for seed in captured["seeds"]:
        pairs = [
            (rec["confidence"], bool(rec["correct"]))
            for rec in captured["records"]
            if rec["seed"] == seed and not rec["error"] and rec["confidence"] is not None
        ]
        per_seed_ece[str(seed)] = {
            "n_pairs": len(pairs),
            "ece": round(compute_ece(pairs), 6),
            "brier": round(compute_brier(pairs), 6),
        }

    usage = captured["usage"]
    cost = _usd(usage, captured["model"])
    n_calls = captured["target_calls"]
    return {
        "per_row": per_row,
        "n_rows": len(per_row),
        "n_seed_stable": len(stable),
        "n_unstable": sum(1 for r in per_row if r["verdict"] == "unstable"),
        "n_errored": sum(1 for r in per_row if r["verdict"] == "error"),
        "n_pass": sum(1 for r in per_row if r["verdict"] == "pass"),
        "n_fail": sum(1 for r in per_row if r["verdict"] == "fail"),
        "difficulty_strata": strata,
        "per_seed_ece": per_seed_ece,
        "divergence_from_beaaa33020": [
            level for level, e in strata.items() if e.get("reproduces") is False
        ],
        "cost": {
            "target_calls": n_calls,
            "judge_calls": 0,
            "prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
            "usd_total": cost,
            "usd_per_call": round(cost / n_calls, 8) if cost is not None and n_calls else None,
            "usd_per_row_at_n5": round(cost / len(per_row), 8) if cost is not None and per_row else None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="openai/gpt-4.1-mini")
    parser.add_argument("--seeds", default=",".join(str(s) for s in DEFAULT_SEEDS))
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts" / "beaaa-35665")
    parser.add_argument("--map-only", action="store_true", help="MAP and exit; no target calls")
    parser.add_argument(
        "--pool-cap-usd",
        type=float,
        required=True,
        help="authorized USD cap for this run; checked against the live "
             "OpenRouter account pool before the first paid call (BEAAA-36019)",
    )
    args = parser.parse_args()
    from harness.openrouter_pool import preflight as pool_preflight

    pool_preflight(args.pool_cap_usd, label="BEAAA-35665 cc capture", slice_id="beaaa-35665")

    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    rows = map_rows()
    print(f"MAP: {len(rows)} {FAMILY} rows, oracle={EXPECTED_ORACLE}, revision={PINNED_SOURCE_REVISION}")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "cc_slice_map.json").write_text(
        json.dumps({"family_id": FAMILY, "n_rows": len(rows), "rows": rows}, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.map_only:
        print("--map-only: no target calls made")
        return 0

    print(f"CAPTURE: {len(rows)} rows x {len(seeds)} seeds = {len(rows)*len(seeds)} target calls, 0 judge calls")
    captured = capture(rows, seeds=seeds, model=args.model)
    result = validate(rows, captured)
    (args.out / "cc_slice_capture.json").write_text(
        json.dumps(captured, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.out / "cc_slice_validation.json").write_text(
        json.dumps({k: v for k, v in result.items() if k != "per_row"}, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.out / "cc_slice_per_row.json").write_text(
        json.dumps(result["per_row"], indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in result.items() if k != "per_row"}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
