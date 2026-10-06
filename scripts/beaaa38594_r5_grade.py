"""BEAAA-38594: r=5 verdict-spread grader.

Replays captured responses from the Phase 1 ledger through each family's own
judge and aggregation. Never re-implements an aggregation function.

  self_contradiction_pairs  → judge_pair + _aggregate
  citation_integrity_legal  → run_f1_existence + run_f2_content_fidelity + aggregate_dim
  hallucination_legal       → run_f2_case_citation (hl.f2.* only, 30 rows)
                              _judge_materialized_text_case for hl.f1 / hl.f3 / hl.f4 / wave0

Import-safe (__main__ guard). Metered against the same USD cap as Phase 1.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.bench import self_contradiction_pairs as scp_mod
from harness.bench import citation_integrity_legal as cil_mod
from harness.bench import hallucination_legal as hl_mod
from tools.content_candidate_inventory import materialize_candidate_case
from tools.registered_case_executor import default_judge_runner, _judge_materialized_text_case

CATALOGUE = ROOT / "artifacts" / "beaaa-18720" / "unified-probe-inventory.jsonl"
HL_YAML = ROOT / "rubric" / "probe_mappings" / "hallucination_legal.yaml"
CIL_YAML = ROOT / "rubric" / "probe_mappings" / "citation_integrity_legal.yaml"
USD_PER_JUDGE_CALL = 0.001228  # measured on BEAAA-35963


class ReplaySubject:
    """A ModelRunner that returns a pre-captured response by prompt text."""
    def __init__(self, responses: dict[str, str]) -> None:
        self._responses = responses

    def model_id(self) -> str:
        return "replay:captured"

    def complete(self, prompt: str) -> str:
        r = self._responses.get(prompt)
        if r is None:
            raise KeyError(f"No captured response for prompt: {prompt[:80]!r}")
        return r


def load_catalogue_by_family(family: str) -> dict[str, Any]:
    rows = (json.loads(l) for l in CATALOGUE.read_text(encoding="utf-8").splitlines() if l.strip())
    return {r["stable_case_id"]: r for r in rows if r["family_id"] == family}


def cil_f1_label(trace_item: dict[str, Any]) -> str:
    """One comparable label for a CIL F1 trace record.

    The record carries `citations: [{plausibility, semantic, score, ...}]` and no
    `judge` key, so the judged labels sit one level down. A seed whose response
    contained no citation has nothing judged and yields a placeholder.
    """
    cites = trace_item.get("citations") or []
    labels = [str(c.get("plausibility", "?")) for c in cites if isinstance(c, dict)]
    labels = _real_labels(labels)
    return "|".join(labels) if labels else "?"


def cil_f2_label(trace_item: dict[str, Any]) -> str:
    """One comparable label for a CIL F2 trace record, from its three axes."""
    axes = trace_item.get("axes")
    if not isinstance(axes, dict):
        return "?"
    picked = [str(axes.get(k, "?")) for k in ("proposition", "scope", "no_fabricated_content")]
    return "|".join(picked) if _real_labels(picked) else "?"


PLACEHOLDER_LABELS = {"?", "", "None", "none"}


def _real_labels(labels: list[str]) -> list[str]:
    """Drop placeholder labels; see _unanimity for why this matters."""
    return [x for x in labels if str(x) not in PLACEHOLDER_LABELS]


def _unanimity(per_case_labels: list[list[str]]) -> dict[str, Any]:
    """Judge disagreement across replicates, refusing to score placeholders.

    BEAAA-38594: the first r=5 run reported `non_unanimous_share: 0.0` for
    citation_integrity_legal, which read as "every judge triple agreed". It was
    not a reading at all - the extraction had defaulted every label to "?" (the
    CIL trace carries no `judge` key), so the share was unanimity over a literal
    question mark. A fabricated 0.0 is worse than a null: it looks like the
    strongest possible evidence.

    So: a case counts only if it has at least two non-placeholder labels, and
    with nothing comparable the share is None rather than 0.0.
    """
    comparable = [lab for lab in (_real_labels(c) for c in per_case_labels) if len(lab) >= 2]
    without = len(per_case_labels) - len(comparable)
    if not comparable:
        return {
            "non_unanimous_share": None,
            "cases_compared": 0,
            "cases_without_labels": without,
            "reason": "no case carried two or more real labels; share is unread, not 0.0",
        }
    n_non_unani = sum(1 for labels in comparable if len(set(labels)) > 1)
    out = {
        "non_unanimous_share": n_non_unani / len(comparable),
        "cases_compared": len(comparable),
        "cases_without_labels": without,
    }
    if without:
        out["reason"] = f"{without} case(s) carried no comparable label and are excluded"
    return out


def _live_rates(per_rep: list[dict[str, Any]]) -> tuple[list[float], list[dict[str, Any]]]:
    """Split replicate records into usable rates and excluded ones.

    BEAAA-38594: CIL replicate 1 came back `f1_status=inconclusive` while 2-5
    were `live`, and its rate (0.2333 against a 0.60-0.62 cluster) was folded
    into min/median/max anyway. An arm that did not produce a live reading is
    not a low measurement of the family; it is not a measurement.
    """
    valid: list[float] = []
    excluded: list[dict[str, Any]] = []
    for rec in per_rep:
        statuses = {k: v for k, v in rec.items() if k.endswith("_status")}
        bad = {k: v for k, v in statuses.items() if v != "live"}
        if rec.get("rate") is None:
            excluded.append({**rec, "excluded_because": "no rate"})
        elif bad:
            excluded.append({**rec, "excluded_because": "arm not live: " + ", ".join(f"{k}={v}" for k, v in sorted(bad.items()))})
        else:
            valid.append(rec["rate"])
    return valid, excluded


def grade_scp(rows_by_rep: dict[int, list[dict]], judge, cap_usd: float, spend: float) -> dict:
    """Grade self_contradiction_pairs; return per-replicate rates and spread."""
    replicates = sorted(rows_by_rep)
    per_rep_rates: list[float | None] = []
    per_case_labels: dict[str, list[str]] = {}  # case_id -> list[label across reps]

    for rep in replicates:
        rows = rows_by_rep[rep]
        per_pair_records: list[dict] = []
        for row in rows:
            spec = materialize_candidate_case("self_contradiction_pairs", row["stable_case_id"])
            exp_obs = spec.expected.get("expected_observable_behavior", {})
            expectation = exp_obs.get("consistency_expectation", "must_match")
            prompts = row["prompts"]
            responses = row["responses"]
            step = 3 * USD_PER_JUDGE_CALL  # ensemble_n=3
            if spend + step > cap_usd:
                print(f"[SCP rep={rep}] cap reached at spend=USD {spend:.4f}", flush=True)
                return {"capped": True, "spend": spend}
            verdict = scp_mod.judge_pair(
                judge,
                expectation,
                responses[0],
                responses[1],
                underlying_question=prompts[0],
                ensemble_n=3,
            )
            spend += step
            per_pair_records.append({**row, "label": verdict["label"], "votes": verdict["votes"]})
            per_case_labels.setdefault(row["stable_case_id"], []).append(verdict["label"])
            print(f"  scp r{rep} {row['stable_case_id']} -> {verdict['label']} spend=USD {spend:.4f}", flush=True)
        agg = scp_mod._aggregate(per_pair_records)
        per_rep_rates.append(agg.get("pass_rate"))

    valid = [r for r in per_rep_rates if r is not None]
    unanimity = _unanimity(list(per_case_labels.values()))
    return {
        "family": "self_contradiction_pairs",
        "per_rep_rates": per_rep_rates,
        "rate_min": min(valid) if valid else None,
        "rate_median": statistics.median(valid) if valid else None,
        "rate_max": max(valid) if valid else None,
        "spend_judge_usd": spend,
        "capped": False,
        **unanimity,
    }


def grade_cil(rows_by_rep: dict[int, list[dict]], judge, cap_usd: float, spend: float,
              index: Any | None = None) -> dict:
    """Grade citation_integrity_legal via run_f1_existence + run_f2_content_fidelity + aggregate_dim."""
    from harness import citation_index as ci
    if index is None:
        index = ci.get_index()

    f1_probes = cil_mod.load_family_probes(CIL_YAML, "F1_existence")
    f2_probes = cil_mod.load_family_probes(CIL_YAML, "F2_content_fidelity")

    # Build a map: probe_id -> probe object, so we can match rows to probes
    f1_by_id = {p.id: p for p in f1_probes}
    f2_by_id = {p.id: p for p in f2_probes}

    replicates = sorted(rows_by_rep)
    per_rep_rates: list[float | None] = []
    per_case_labels: dict[str, list[str]] = {}

    # Probes are per case_id; the catalogue case_id should match a probe id
    # Check the probe id to case_id mapping
    cat = load_catalogue_by_family("citation_integrity_legal")

    for rep in replicates:
        rows = rows_by_rep[rep]
        # Build response lookup for replay subject
        resp_by_prompt: dict[str, str] = {}
        for row in rows:
            if row.get("responses"):
                # Each CIL case has 1 prompt and 1 response
                resp_by_prompt[row["prompts"][0]] = row["responses"][0]

        subject = ReplaySubject(resp_by_prompt)

        # Run F1 and F2 through the family's own functions (they call subject.complete internally)
        f1_step = len(f1_probes) * 3 * USD_PER_JUDGE_CALL
        f2_step = len(f2_probes) * 3 * USD_PER_JUDGE_CALL
        if spend + f1_step + f2_step > cap_usd:
            print(f"[CIL rep={rep}] cap would exceed: spend={spend:.4f}", flush=True)
            return {"capped": True, "spend": spend}

        f1_rollup = cil_mod.run_f1_existence(subject, judge, f1_probes, index=index, ensemble_n=3)
        spend += f1_step
        f2_rollup = cil_mod.run_f2_content_fidelity(subject, judge, f2_probes, ensemble_n=3)
        spend += f2_step

        dim = cil_mod.aggregate_dim(f1_rollup, f2_rollup)
        rate = dim.get("dim_pass_rate")
        per_rep_rates.append(rate)
        per_rep_records.append({
            "rep": rep,
            "rate": rate,
            "f1_status": f1_rollup.get("status"),
            "f2_status": f2_rollup.get("status"),
        })

        # Per-case labels come out of the trace records themselves. The CIL
        # trace has no `judge` key: F1 keeps its judged labels one level down in
        # `citations[].plausibility`, and F2 keeps its three axes in `axes`.
        for item in f1_rollup.get("trace", []):
            case_id = item.get("id") or item.get("seed_id", "?")
            per_case_labels.setdefault(case_id, []).append(cil_f1_label(item))
        for item in f2_rollup.get("trace", []):
            case_id = item.get("id", "?")
            per_case_labels.setdefault(case_id, []).append(cil_f2_label(item))

        print(f"  cil r{rep} dim_rate={rate} f1_status={f1_rollup.get('status')} f2_status={f2_rollup.get('status')} spend=USD {spend:.4f}", flush=True)

    valid, excluded = _live_rates(per_rep_records)
    unanimity = _unanimity(list(per_case_labels.values()))
    return {
        "family": "citation_integrity_legal",
        "per_rep_rates": per_rep_rates,
        "per_rep_records": per_rep_records,
        "excluded_replicates": excluded,
        "rate_min": min(valid) if valid else None,
        "rate_median": statistics.median(valid) if valid else None,
        "rate_max": max(valid) if valid else None,
        "spend_judge_usd": spend,
        "capped": False,
        **unanimity,
    }


def grade_hl(rows_by_rep: dict[int, list[dict]], judge, cap_usd: float, spend: float) -> dict:
    """Grade hallucination_legal.

    hl.f2.* (30 rows) → run_f2_case_citation via a replay subject.
    All other rows (hl.f1, hl.f3, hl.f4, wave0) → _judge_materialized_text_case.
    """
    from harness import citation_index as ci
    index = ci.get_index()

    f2_probes = hl_mod.load_f2_probes(HL_YAML)
    f2_by_prompt: dict[str, Any] = {}
    for p in f2_probes:
        f2_by_prompt[p.query] = p

    replicates = sorted(rows_by_rep)
    per_rep_rates_f2: list[float | None] = []
    per_rep_rates_other: list[float | None] = []
    per_case_labels: dict[str, list[str]] = {}

    for rep in replicates:
        rows = rows_by_rep[rep]
        f2_rows = [r for r in rows if r["stable_case_id"].startswith("hl.f2.")]
        other_rows = [r for r in rows if not r["stable_case_id"].startswith("hl.f2.")]

        # F2: use replay subject with run_f2_case_citation
        if f2_rows:
            resp_map = {r["prompts"][0]: r["responses"][0] for r in f2_rows if r.get("responses")}
            subject = ReplaySubject(resp_map)
            step = len(f2_rows) * 3 * USD_PER_JUDGE_CALL
            if spend + step > cap_usd:
                return {"capped": True, "spend": spend}
            f2_rollup = hl_mod.run_f2_case_citation(subject, judge, f2_probes, index=index, ensemble_n=3)
            spend += step
            per_rep_rates_f2.append(f2_rollup.get("pass_rate"))
            for item in f2_rollup.get("trace", []):
                per_case_labels.setdefault(item["id"], []).append(item.get("judge", {}).get("label", "?"))
            print(f"  hl r{rep} f2 pass_rate={f2_rollup.get('pass_rate'):.3f} n_fab={f2_rollup.get('n_fabrications')} spend=USD {spend:.4f}", flush=True)

        # Other: _judge_materialized_text_case per row
        other_scores: list[float] = []
        for row in other_rows:
            spec = materialize_candidate_case("hallucination_legal", row["stable_case_id"])
            step = 3 * USD_PER_JUDGE_CALL
            if spend + step > cap_usd:
                return {"capped": True, "spend": spend}
            result = _judge_materialized_text_case(
                judge,
                prompts=row["prompts"],
                responses=row["responses"],
                expected=spec.expected,
                judge_criteria=str(spec.oracle.get("spec", {}).get("judge", {})),
                applicability=spec.applicability,
            )
            spend += step
            other_scores.append(result["score"])
            per_case_labels.setdefault(row["stable_case_id"], []).append(result["verdict"])
        if other_scores:
            per_rep_rates_other.append(sum(other_scores) / len(other_scores))

    # Combine: weight by row count
    n_f2 = 30
    n_other = max(0, sum(1 for r in rows_by_rep.get(1, []) if not r["stable_case_id"].startswith("hl.f2.")))
    n_total = n_f2 + n_other
    per_rep_rates_combined: list[float | None] = []
    for i, _ in enumerate(replicates):
        f2 = per_rep_rates_f2[i] if i < len(per_rep_rates_f2) else None
        oth = per_rep_rates_other[i] if i < len(per_rep_rates_other) else None
        if f2 is not None and oth is not None and n_total > 0:
            per_rep_rates_combined.append((f2 * n_f2 + oth * n_other) / n_total)
        elif f2 is not None:
            per_rep_rates_combined.append(f2)
        elif oth is not None:
            per_rep_rates_combined.append(oth)
        else:
            per_rep_rates_combined.append(None)

    valid_f2 = [r for r in per_rep_rates_f2 if r is not None]
    valid_other = [r for r in per_rep_rates_other if r is not None]
    valid_combined = [r for r in per_rep_rates_combined if r is not None]
    unanimity = _unanimity(list(per_case_labels.values()))
    return {
        "family": "hallucination_legal",
        "per_rep_rates_f2_only": per_rep_rates_f2,
        "per_rep_rates_other": per_rep_rates_other,
        "per_rep_rates_combined": per_rep_rates_combined,
        "f2_rate_min": min(valid_f2) if valid_f2 else None,
        "f2_rate_median": statistics.median(valid_f2) if valid_f2 else None,
        "f2_rate_max": max(valid_f2) if valid_f2 else None,
        "rate_min": min(valid_combined) if valid_combined else None,
        "rate_median": statistics.median(valid_combined) if valid_combined else None,
        "rate_max": max(valid_combined) if valid_combined else None,
        "non_unanimous_share": non_unani,
        "spend_judge_usd": spend,
        "capped": False,
    }


def group_by_rep(rows: list[dict]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["replicate"], []).append(r)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cap-usd", type=float, default=5.00)
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts" / "beaaa-38568")
    parser.add_argument("--phase1-spend", type=float, default=0.0, help="USD already spent in phase 1")
    args = parser.parse_args()

    ledger = args.out / "r5_capture_ledger.jsonl"
    if not ledger.exists():
        print("ERROR: phase 1 ledger not found", flush=True)
        return 1

    all_rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    ok_rows = [r for r in all_rows if "error" not in r and r.get("responses")]
    print(f"Ledger: {len(all_rows)} total rows, {len(ok_rows)} ok, {len(all_rows)-len(ok_rows)} error/no-response", flush=True)

    scp_rows = [r for r in ok_rows if r["family_id"] == "self_contradiction_pairs"]
    cil_rows = [r for r in ok_rows if r["family_id"] == "citation_integrity_legal"]
    hl_rows  = [r for r in ok_rows if r["family_id"] == "hallucination_legal"]
    print(f"scp={len(scp_rows)} cil={len(cil_rows)} hl={len(hl_rows)}", flush=True)

    judge = default_judge_runner()
    print(f"judge={judge.model_id()}", flush=True)

    spend = args.phase1_spend
    results: dict[str, Any] = {"phase1_spend_usd": args.phase1_spend}

    print("\n=== Grading self_contradiction_pairs ===", flush=True)
    scp_result = grade_scp(group_by_rep(scp_rows), judge, args.cap_usd, spend)
    spend = scp_result.get("spend_judge_usd", spend)
    results["self_contradiction_pairs"] = scp_result
    if scp_result.get("capped"):
        results["capped"] = True
        print("Capped during SCP grading", flush=True)
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "r5_report.json").write_text(json.dumps(results, indent=2))
        return 0

    print("\n=== Grading citation_integrity_legal ===", flush=True)
    cil_result = grade_cil(group_by_rep(cil_rows), judge, args.cap_usd, spend)
    spend = cil_result.get("spend_judge_usd", spend)
    results["citation_integrity_legal"] = cil_result
    if cil_result.get("capped"):
        results["capped"] = True
        (args.out / "r5_report.json").write_text(json.dumps(results, indent=2))
        return 0

    print("\n=== Grading hallucination_legal ===", flush=True)
    hl_result = grade_hl(group_by_rep(hl_rows), judge, args.cap_usd, spend)
    spend = hl_result.get("spend_judge_usd", spend)
    results["hallucination_legal"] = hl_result
    if hl_result.get("capped"):
        results["capped"] = True

    results["total_actual_spend_usd"] = spend
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "r5_report.json").write_text(json.dumps(results, indent=2))
    print(f"\nReport written to {args.out}/r5_report.json", flush=True)
    print(f"Total spend: USD {spend:.4f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
