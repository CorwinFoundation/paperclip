"""BEAAA-38594: r=5 target capture for hallucination_legal, citation_integrity_legal and
self_contradiction_pairs against the BEAAA-35963 Vapi target (assistant 09aea361).

Phase 1 (this script): dispatch every materialised prompt -- BOTH arms for a pair family --
and append one record per (case, replicate) to a resumable JSONL ledger. No judge calls.
Phase 2 (``scripts/beaaa38594_r5_grade.py``) grades the ledger through each family's own
judge and aggregation in ``harness/bench/<family>.py`` by replaying the captured responses.

Import-safe (``__main__`` guard); never imports ``scripts/beaaa33673_*``. Spend is metered
per target call against a hard cap shared with phase 2 (``--cap-usd``, default 5.00).
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

from harness.customer_runner import _VapiCustomerAgentRunner
from tools.content_candidate_inventory import materialize_candidate_case

CATALOGUE = ROOT / "artifacts" / "beaaa-18720" / "unified-probe-inventory.jsonl"
FAMILIES = ("self_contradiction_pairs", "citation_integrity_legal", "hallucination_legal")
PAIR_FAMILIES = {"self_contradiction_pairs"}
ASSISTANT = "09aea361-04b3-4a62-bcb0-2f2582f11ec8"
TARGET_ID = "vapi:09aea361@api.vapi.ai"
TARGET_MODEL_VERSION = "vapi-assistant:09aea361-04b3-4a62-bcb0-2f2582f11ec8"
USD_PER_TARGET_CALL = 4.909e-05  # measured on BEAAA-35963


def case_ids(family: str) -> list[str]:
    rows = (json.loads(line) for line in CATALOGUE.read_text(encoding="utf-8").splitlines() if line.strip())
    return sorted(r["stable_case_id"] for r in rows if r["family_id"] == family)


def ledger_rows(ledger: Path) -> list[dict]:
    if not ledger.exists():
        return []
    return [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replicates", type=int, default=5)
    parser.add_argument("--family", action="append", choices=FAMILIES)
    parser.add_argument("--limit", type=int, default=0, help="first N cases per family (smoke)")
    parser.add_argument("--cap-usd", type=float, default=5.00)
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts" / "beaaa-38568")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    ledger = args.out / "r5_capture_ledger.jsonl"
    existing = ledger_rows(ledger)
    already = {(r["stable_case_id"], r["replicate"]) for r in existing if "error" not in r}
    spend = sum(r.get("target_calls", 0) for r in existing) * USD_PER_TARGET_CALL

    subject = _VapiCustomerAgentRunner(secret=os.environ["VAPI_API_KEY"], assistant_id=ASSISTANT)
    print(f"target={subject.model_id()} cap USD {args.cap_usd:.2f}; {len(already)} rows in ledger", flush=True)
    started, written = time.time(), 0
    for family in args.family or FAMILIES:
        cases = case_ids(family)[: args.limit or None]
        for replicate in range(1, args.replicates + 1):
            for case_id in cases:
                if (case_id, replicate) in already:
                    continue
                spec = materialize_candidate_case(family, case_id)
                prompts = list(spec.prompts)
                if family in PAIR_FAMILIES and len(prompts) != 2:
                    raise ValueError(f"{case_id}: pair family needs 2 prompts, got {len(prompts)}")
                step = len(prompts) * USD_PER_TARGET_CALL
                if spend + step > args.cap_usd:
                    print(f"STOPPING at cap: spend USD {spend:.4f} + {step:.4f} > {args.cap_usd:.2f}", flush=True)
                    return 0
                record = {"stable_case_id": case_id, "replicate": replicate, "family_id": family,
                          "target_id": TARGET_ID, "model_version": TARGET_MODEL_VERSION,
                          "prompts": prompts}
                try:
                    record["responses"] = [subject.complete(p) for p in prompts]
                except Exception as exc:  # noqa: BLE001 - recorded, retried on resume
                    record["error"] = f"{type(exc).__name__}: {exc}"
                record["target_calls"] = len(prompts)
                record["captured_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                spend += step
                with ledger.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, sort_keys=True) + "\n")
                written += 1
                print(f"[{written:4d}] {family} {case_id} r{replicate} "
                      f"{'ERR ' + record['error'][:80] if 'error' in record else 'ok'} "
                      f"spend=USD {spend:.4f} elapsed={time.time()-started:.0f}s", flush=True)
    print(f"DONE {written} rows written this run; target spend USD {spend:.4f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
