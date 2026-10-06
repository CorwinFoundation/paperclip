"""BEAAA-35963: re-capture the 60 counterfactual_stability pairs with BOTH arms dispatched.

Why this script exists
----------------------
``scripts/beaaa33673_capture.py:91`` reads::

    prompts = fields.get("prompts") or fields.get("prompt_turn_sequence") or []

``materialize_candidate_case("counterfactual_stability", case_id)`` returns **two**
prompts for a pair, but when ``prompts`` is absent from the materialised dataclass the
fallback picks up the raw catalogue turn -- a single dict holding both ``pair_a`` and
``pair_b`` -- so one blob went to the target and one response came back. The row was then
graded by ``_judge_materialized_text_case``, a generic single-text judge, instead of
``judge_pair``. The pairing never ran for any of the 60 rows: ``Yelena``, ``Kovalenko``,
``Akiko`` and ``Tanaka`` appear in 0 of 607 recorded responses, and ``cs.pair.ne.09``
answers "Hi Jennifer!" -- arm A only.

So the 10.6% pass rate recorded for this family is not a measurement. This script produces
the measurement: both arms dispatched, graded through ``judge_pair``.

Two properties this script has that the one it replaces did not
--------------------------------------------------------------
* **It is import-safe.** ``beaaa33673_capture.py`` and ``beaaa33673_controls.py`` execute
  at module scope with no ``__main__`` guard; importing one of them to read its dispatch
  path started a live capture and spent 5 attempts against the round-1 budget ledger.
* **It is resumable and metered.** Every pair is appended to a JSONL ledger the moment it
  completes, and a restart skips what is already there. Spend is accumulated against a
  hard cap and the run stops rather than overrunning it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.bench.counterfactual_pairs import judge_pair
from harness.customer_runner import _VapiCustomerAgentRunner
from tools.content_candidate_inventory import materialize_candidate_case
from tools.registered_case_executor import default_judge_runner

CATALOGUE = ROOT / "artifacts" / "beaaa-18720" / "unified-probe-inventory.jsonl"
FAMILY = "counterfactual_stability"
ASSISTANT = "09aea361-04b3-4a62-bcb0-2f2582f11ec8"
TARGET_ID = "vapi:09aea361@api.vapi.ai"
TARGET_MODEL_VERSION = "vapi-assistant:09aea361-04b3-4a62-bcb0-2f2582f11ec8"

# Measured on BEAAA-35963: USD per target call, and the judge call priced from the real
# prompt shape (154 system + 65 instruction + two responses averaging 218 tokens, ~140 out).
USD_PER_TARGET_CALL = 4.909e-05
USD_PER_JUDGE_CALL = 0.001228

# This family does NOT score its labels as a binary pass/fail. Its own aggregation in
# ``harness/bench/counterfactual_pairs.py`` is
# ``stability_rate = (stable + 0.5 * drift_minor) / denominator`` -- ``drift_minor`` is
# cosmetic phrasing drift with identical actionable content, so it earns half credit
# rather than a FAIL. Recording a per-row boolean here would silently re-define the
# oracle: on this capture a stable-only mapping reads 6.0% where the family's own rule
# reads 34.2%. So each row carries the weight the family assigns it and the rate is
# computed at aggregate level, by the family's function, never by this script.
STABILITY_WEIGHT = {"stable": 1.0, "drift_minor": 0.5, "drift_decision": 0.0,
                    "drift_tone": 0.0, "inconclusive": None}


def case_ids() -> list[str]:
    rows = (json.loads(line) for line in CATALOGUE.read_text(encoding="utf-8").splitlines() if line.strip())
    return sorted(r["stable_case_id"] for r in rows if r["family_id"] == FAMILY)


def done_keys(ledger: Path) -> set[tuple[str, int]]:
    if not ledger.exists():
        return set()
    out = set()
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        out.add((row["stable_case_id"], row["replicate"]))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replicates", type=int, default=5)
    parser.add_argument("--ensemble-n", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0, help="first N cases only (smoke)")
    parser.add_argument("--cap-usd", type=float, default=1.50)
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts" / "beaaa-35963")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    ledger = args.out / "cs_pair_recapture_ledger.jsonl"
    already = done_keys(ledger)

    cases = case_ids()
    if args.limit:
        cases = cases[: args.limit]

    subject = _VapiCustomerAgentRunner(secret=os.environ["VAPI_API_KEY"], assistant_id=ASSISTANT)
    judge = default_judge_runner()
    print(f"target={subject.model_id()} judge={judge.model_id()}", flush=True)
    print(
        f"{len(cases)} cases x {args.replicates} replicates, ensemble_n={args.ensemble_n}, "
        f"cap USD {args.cap_usd:.2f}; {len(already)} pair-replicates already in ledger",
        flush=True,
    )

    spend = len(already) * (2 * USD_PER_TARGET_CALL + args.ensemble_n * USD_PER_JUDGE_CALL)
    started = time.time()
    written = 0
    for case_id in cases:
        for replicate in range(1, args.replicates + 1):
            if (case_id, replicate) in already:
                continue
            step = 2 * USD_PER_TARGET_CALL + args.ensemble_n * USD_PER_JUDGE_CALL
            if spend + step > args.cap_usd:
                print(
                    f"STOPPING at cap: spend USD {spend:.4f} + step USD {step:.4f} > "
                    f"cap USD {args.cap_usd:.2f}. {written} written this run.",
                    flush=True,
                )
                return 0
            spec = materialize_candidate_case(FAMILY, case_id)
            if len(spec.prompts) != 2:
                raise ValueError(f"{case_id}: expected 2 prompts, got {len(spec.prompts)}")
            try:
                responses = [subject.complete(p) for p in spec.prompts]
                observable = spec.expected.get("expected_observable_behavior", {})
                judged = judge_pair(
                    judge,
                    responses[0],
                    responses[1],
                    underlying_scenario=str(observable.get("scenario_id") or case_id),
                    ensemble_n=args.ensemble_n,
                )
                record = {
                    "stable_case_id": case_id,
                    "replicate": replicate,
                    "family_id": FAMILY,
                    "target_id": TARGET_ID,
                    "model_version": TARGET_MODEL_VERSION,
                    "judge_model": judge.model_id(),
                    "ensemble_n": args.ensemble_n,
                    "axis": str(observable.get("axis") or ""),
                    "scenario_id": str(observable.get("scenario_id") or ""),
                    "prompt_a": spec.prompts[0],
                    "prompt_b": spec.prompts[1],
                    "response_a": responses[0],
                    "response_b": responses[1],
                    "judge_label": judged["label"],
                    "judge_votes": judged["votes"],
                    "stability_weight": STABILITY_WEIGHT.get(judged["label"]),
                    "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                spend += step
            except Exception as exc:  # noqa: BLE001 - a failed pair is recorded, not fatal
                record = {
                    "stable_case_id": case_id,
                    "replicate": replicate,
                    "family_id": FAMILY,
                    "error": f"{type(exc).__name__}: {exc}",
                    "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                spend += 2 * USD_PER_TARGET_CALL
            with ledger.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
            written += 1
            flag = record.get("judge_label") or record.get("error", "?")
            print(
                f"[{written:4d}] {case_id} r{replicate} {flag} "
                f"spend=USD {spend:.4f} elapsed={time.time()-started:.0f}s",
                flush=True,
            )
    print(f"DONE {written} pair-replicates written this run; spend USD {spend:.4f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
