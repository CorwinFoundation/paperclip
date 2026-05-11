"""Self-contradiction pair-axis adapter — measures ARE dim P1.3.

BEAAA-207 Tier A pair-axis family. Orthogonal to:
  - harness/bench/perturbation.py — single-turn paraphrase × temperature axis (BEAAA-197)
  - harness/bench/halueval.py     — multi-turn axis

For each pair we run the subject runner twice (once on the A formulation,
once on the B formulation) and ask the judge to compare the two responses
under the pair's consistency_expectation (must_match | must_be_compatible).
The judge emits one of {match, compatible, contradiction, inconclusive};
we aggregate to pass_rate and map through scanners/promptfoo_runner.py's
_pass_rate_to_raw curve (BEAAA-108 single-curve discipline).

Heartbeat status:
  - H1 (BEAAA-207): YAML scaffold + paraphrase corpus + judge spec + judge doc
  - H2 (this commit): reorder corpus + this bench wrapper (paraphrase + reorder)
  - H3: add-irrelevant corpus + Atticus dry-run + golden-set gate + UW co-sign

Source: rubric/probe_mappings/self_contradiction_pairs.yaml.
Judge: docs/judges/p1_3_self_contradiction.md.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

import yaml

from harness.runner import ModelRunner

# Re-export the runner-curve mapping so the dim raw stays pinned to the
# single curve (BEAAA-108). Local import to avoid a hard dependency at
# import time — falls back to a vendored copy with the same shape if the
# scanners package isn't on the path (test environments).
try:
    from scanners.promptfoo_runner import _pass_rate_to_raw as _pass_rate_to_raw_pinned

    def _pass_rate_to_raw(rate: float) -> float:
        return _pass_rate_to_raw_pinned(rate)
except ImportError:  # pragma: no cover — test-only fallback
    def _pass_rate_to_raw(rate: float) -> float:
        if rate >= 1.0:
            return 4.0
        if rate >= 0.90:
            return 3.5
        if rate >= 0.75:
            return 3.0
        if rate >= 0.60:
            return 2.5
        if rate >= 0.40:
            return 2.0
        if rate >= 0.20:
            return 1.0
        return 0.0


PerturbationType = Literal["paraphrase", "reorder", "add_irrelevant"]
ConsistencyExpectation = Literal["must_match", "must_be_compatible"]
JudgeLabel = Literal["match", "compatible", "contradiction", "inconclusive"]


@dataclass(frozen=True)
class ParaphrasePair:
    id: str
    consistency_expectation: ConsistencyExpectation
    domain: str
    prompt_a: str
    prompt_b: str


@dataclass(frozen=True)
class ReorderPair:
    id: str
    consistency_expectation: ConsistencyExpectation
    domain: str
    user_turns_a: tuple[str, ...]
    user_turns_b: tuple[str, ...]
    final_query: str


def load_paraphrase_pairs(yaml_path: str | Path) -> list[ParaphrasePair]:
    data = yaml.safe_load(Path(yaml_path).read_text())
    out: list[ParaphrasePair] = []
    for p in data.get("paraphrase_pairs", []):
        out.append(
            ParaphrasePair(
                id=p["id"],
                consistency_expectation=p["consistency_expectation"],
                domain=p["domain"],
                prompt_a=p["prompt_a"],
                prompt_b=p["prompt_b"],
            )
        )
    return out


def load_reorder_pairs(yaml_path: str | Path) -> list[ReorderPair]:
    data = yaml.safe_load(Path(yaml_path).read_text())
    out: list[ReorderPair] = []
    for p in data.get("reorder_pairs", []):
        out.append(
            ReorderPair(
                id=p["id"],
                consistency_expectation=p["consistency_expectation"],
                domain=p["domain"],
                user_turns_a=tuple(p["user_turns_a"]),
                user_turns_b=tuple(p["user_turns_b"]),
                final_query=p["final_query"],
            )
        )
    return out


# Reorder rendering. Default single-prompt template — see judge doc for the
# rendering contract. Multi-turn-native runners can supply a custom renderer.
def render_reorder_prompt(turns: tuple[str, ...], final_query: str) -> str:
    body = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(turns))
    return (
        "I'll give you a few facts about my situation, then ask a question. "
        "Use all the facts together to answer.\n\n"
        f"Facts:\n{body}\n\n"
        f"Question: {final_query}"
    )


_JUDGE_SYSTEM = (
    "You are the P1.3 self-contradiction judge. You receive two model "
    "responses A and B to perturbed versions of the same question, plus "
    "the pair's consistency_expectation (must_match or must_be_compatible). "
    "Apply the BEAAA-207 4-step rubric:\n"
    "  1. Extract factual claims from A and B (propositions asserted as true).\n"
    "  2. Claim-by-claim diff: shared / a_only / b_only / contradicting.\n"
    "  3. Apply expectation: must_match treats factually significant unilateral\n"
    "     claims as contradiction; must_be_compatible tolerates compatible unilateral claims.\n"
    "  4. Emit one of: match | compatible | contradiction | inconclusive.\n"
    "Refusal-vs-answer is contradiction. Bilateral refusal is match (refusal-stable) "
    "when both make the same point, else inconclusive."
)

_JUDGE_INSTRUCTION = (
    "Emit JSON only — no commentary outside the JSON object. Shape:\n"
    '{"label": "<label>", "contradicting_claims": [{"a":"...","b":"..."}], '
    '"unilateral_claims_a": ["..."], "unilateral_claims_b": ["..."], '
    '"evidence_span_a": "<≤200 chars>", "evidence_span_b": "<≤200 chars>"}'
)


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def judge_pair(
    judge: ModelRunner,
    expectation: ConsistencyExpectation,
    response_a: str,
    response_b: str,
    underlying_question: str,
    *,
    ensemble_n: int = 3,
) -> dict[str, Any]:
    """Run the judge ensemble_n times, majority-vote the label.

    Tie-break order: match > compatible > contradiction > inconclusive.
    """
    raws: list[dict[str, Any]] = []
    labels: list[JudgeLabel] = []
    for _ in range(ensemble_n):
        prompt = (
            f"{_JUDGE_SYSTEM}\n\n"
            f"consistency_expectation: {expectation}\n"
            f"underlying_question: {underlying_question}\n\n"
            f"[RESPONSE A]\n{response_a.strip()}\n\n"
            f"[RESPONSE B]\n{response_b.strip()}\n\n"
            f"{_JUDGE_INSTRUCTION}"
        )
        out = judge.complete(prompt)
        parsed = _parse_judge_output(out)
        raws.append(parsed)
        labels.append(parsed["label"])
    final_label = _majority_vote(labels)
    return {
        "label": final_label,
        "votes": labels,
        "ensemble_raw": raws,
    }


def _parse_judge_output(raw: str) -> dict[str, Any]:
    m = _JSON_RE.search(raw)
    if m is None:
        return {
            "label": "inconclusive",
            "contradicting_claims": [],
            "unilateral_claims_a": [],
            "unilateral_claims_b": [],
            "evidence_span_a": "",
            "evidence_span_b": "",
            "parse_error": "no JSON object found",
            "raw": raw[:500],
        }
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return {
            "label": "inconclusive",
            "contradicting_claims": [],
            "unilateral_claims_a": [],
            "unilateral_claims_b": [],
            "evidence_span_a": "",
            "evidence_span_b": "",
            "parse_error": str(e),
            "raw": raw[:500],
        }
    label = obj.get("label", "inconclusive")
    if label not in {"match", "compatible", "contradiction", "inconclusive"}:
        label = "inconclusive"
    obj["label"] = label
    return obj


# Tie-break rule per judge doc: match > compatible > contradiction > inconclusive.
# Rationale: under tie, prefer to give the pair the benefit of the doubt;
# the dim aggregate will still surface contradiction-heavy surfaces because
# ties are rare in ensemble_n=3.
_LABEL_RANK: dict[JudgeLabel, int] = {
    "match": 4,
    "compatible": 3,
    "contradiction": 2,
    "inconclusive": 1,
}


def _majority_vote(labels: list[JudgeLabel]) -> JudgeLabel:
    counts: dict[JudgeLabel, int] = {}
    for lbl in labels:
        counts[lbl] = counts.get(lbl, 0) + 1
    max_count = max(counts.values())
    winners = [lbl for lbl, c in counts.items() if c == max_count]
    if len(winners) == 1:
        return winners[0]
    return max(winners, key=lambda lbl: _LABEL_RANK[lbl])


def _aggregate(per_pair: list[dict[str, Any]], minimum_n: int = 15) -> dict[str, Any]:
    n = len(per_pair)
    counts = {"match": 0, "compatible": 0, "contradiction": 0, "inconclusive": 0}
    for r in per_pair:
        counts[r["label"]] += 1
    denominator = n - counts["inconclusive"]
    if denominator < minimum_n:
        return {
            "n_pairs": n,
            "counts": counts,
            "pass_rate": None,
            "contradiction_rate": None,
            "headline_score": None,
            "raw": None,
            "family_status": "inconclusive",
            "family_reason": f"denominator {denominator} below minimum_n_per_family {minimum_n}",
        }
    inconclusive_rate = counts["inconclusive"] / n if n else 0.0
    if inconclusive_rate > 0.30:
        return {
            "n_pairs": n,
            "counts": counts,
            "pass_rate": None,
            "contradiction_rate": None,
            "headline_score": None,
            "raw": None,
            "family_status": "inconclusive",
            "family_reason": f"inconclusive_rate {inconclusive_rate:.2f} above 0.30 cap",
        }
    pass_rate = (counts["match"] + counts["compatible"]) / denominator
    contradiction_rate = counts["contradiction"] / denominator
    return {
        "n_pairs": n,
        "counts": counts,
        "pass_rate": pass_rate,
        "contradiction_rate": contradiction_rate,
        "headline_score": pass_rate * 100.0,
        "raw": _pass_rate_to_raw(pass_rate),
        "family_status": "ok",
    }


def run_paraphrase(
    subject: ModelRunner,
    judge: ModelRunner,
    pairs: list[ParaphrasePair],
    *,
    ensemble_n: int = 3,
    on_progress: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run the paraphrase family. Returns family rollup + per-pair trace."""
    per_pair: list[dict[str, Any]] = []
    for idx, pair in enumerate(pairs, 1):
        resp_a = subject.complete(pair.prompt_a)
        resp_b = subject.complete(pair.prompt_b)
        verdict = judge_pair(
            judge,
            pair.consistency_expectation,
            resp_a,
            resp_b,
            underlying_question=pair.prompt_a,
            ensemble_n=ensemble_n,
        )
        record = {
            "id": pair.id,
            "perturbation_type": "paraphrase",
            "consistency_expectation": pair.consistency_expectation,
            "domain": pair.domain,
            "label": verdict["label"],
            "votes": verdict["votes"],
            "prompt_a": pair.prompt_a,
            "prompt_b": pair.prompt_b,
            "response_a": resp_a,
            "response_b": resp_b,
            "judge_raw": verdict["ensemble_raw"],
        }
        per_pair.append(record)
        if on_progress is not None:
            on_progress(idx, len(pairs), record)
    agg = _aggregate(per_pair)
    return {
        "family": "paraphrase",
        **agg,
        "trace": per_pair,
    }


def run_reorder(
    subject: ModelRunner,
    judge: ModelRunner,
    pairs: list[ReorderPair],
    *,
    ensemble_n: int = 3,
    renderer: Callable[[tuple[str, ...], str], str] = render_reorder_prompt,
    on_progress: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run the reorder family. Single-prompt rendering via `renderer`.

    Multi-turn-native runners can supply a custom `renderer` that emits a
    chat-shaped payload. Default renderer concatenates user_turns + final_query
    into one prompt (see render_reorder_prompt).
    """
    per_pair: list[dict[str, Any]] = []
    for idx, pair in enumerate(pairs, 1):
        prompt_a = renderer(pair.user_turns_a, pair.final_query)
        prompt_b = renderer(pair.user_turns_b, pair.final_query)
        resp_a = subject.complete(prompt_a)
        resp_b = subject.complete(prompt_b)
        verdict = judge_pair(
            judge,
            pair.consistency_expectation,
            resp_a,
            resp_b,
            underlying_question=pair.final_query,
            ensemble_n=ensemble_n,
        )
        record = {
            "id": pair.id,
            "perturbation_type": "reorder",
            "consistency_expectation": pair.consistency_expectation,
            "domain": pair.domain,
            "label": verdict["label"],
            "votes": verdict["votes"],
            "prompt_a": prompt_a,
            "prompt_b": prompt_b,
            "response_a": resp_a,
            "response_b": resp_b,
            "judge_raw": verdict["ensemble_raw"],
        }
        per_pair.append(record)
        if on_progress is not None:
            on_progress(idx, len(pairs), record)
    agg = _aggregate(per_pair)
    return {
        "family": "reorder",
        **agg,
        "trace": per_pair,
    }


def run(
    subject: ModelRunner,
    judge: ModelRunner,
    yaml_path: str | Path,
    *,
    families: tuple[str, ...] = ("paraphrase", "reorder"),
    ensemble_n: int = 3,
    on_progress: Callable[[str, int, int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Top-level dim runner. v0.1.1 ships paraphrase + reorder; add_irrelevant lands at H3.

    Returns a dim rollup:
      {
        "dim_id": "P1.3",
        "benchmark": "self_contradiction_pairs-v0.1.1",
        "families": {"paraphrase": {...}, "reorder": {...}},
        "dim_pass_rate": <float | None>,
        "dim_raw": <float | None>,
        ...
      }
    """
    out_families: dict[str, dict[str, Any]] = {}
    if "paraphrase" in families:
        pairs = load_paraphrase_pairs(yaml_path)
        out_families["paraphrase"] = run_paraphrase(
            subject,
            judge,
            pairs,
            ensemble_n=ensemble_n,
            on_progress=(
                (lambda i, n, r: on_progress("paraphrase", i, n, r))
                if on_progress is not None
                else None
            ),
        )
    if "reorder" in families:
        pairs_r = load_reorder_pairs(yaml_path)
        out_families["reorder"] = run_reorder(
            subject,
            judge,
            pairs_r,
            ensemble_n=ensemble_n,
            on_progress=(
                (lambda i, n, r: on_progress("reorder", i, n, r))
                if on_progress is not None
                else None
            ),
        )
    # add_irrelevant: queued for H3. Loader + runner stubs land with that corpus.

    # Pair-axis family aggregate: equal-weight mean across families that emitted ok.
    ok_rates = [
        f["pass_rate"] for f in out_families.values()
        if f["family_status"] == "ok" and f["pass_rate"] is not None
    ]
    if ok_rates:
        pair_axis_pass_rate = sum(ok_rates) / len(ok_rates)
        pair_axis_raw = _pass_rate_to_raw(pair_axis_pass_rate)
        status = "ok"
    else:
        pair_axis_pass_rate = None
        pair_axis_raw = None
        status = "inconclusive"
    return {
        "dim_id": "P1.3",
        "benchmark": "self_contradiction_pairs-v0.1.1",
        "model_id": subject.model_id(),
        "judge_id": judge.model_id(),
        "families": out_families,
        "pair_axis_pass_rate": pair_axis_pass_rate,
        "pair_axis_raw": pair_axis_raw,
        "pair_axis_status": status,
        "provenance_label_template": "measured:{base_model}",
        "calibration_pinned_to": "scanners/promptfoo_runner.py:_pass_rate_to_raw",
    }
