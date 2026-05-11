"""Self-contradiction-under-perturbation adapter — measures ARE dim P1.3.

BEAAA-197 Track 1. For each seed, the subject runner answers 5 paraphrasings
× 3 temperatures = 15 variants. The judge clusters the 15 responses into
semantic-equivalence buckets. Consistency = max_bucket_size / 15. Mean across
seeds → raw 0-4.

Seed `ps.20` is the BEAAA-193 German-car-accident paraphrase family — it
quantitatively reproduces the qualitative finding from the LawConnect Atticus
round-2 scan.

Source: rubric/probe_mappings/perturbation_seeds.yaml.

Because temperature variation requires distinct sampling configurations,
the runner-construction path is supplied as a factory:

    runner_factory(temperature: float) -> ModelRunner

Callers using a deterministic mock can ignore temperature and return the
same runner; live API callers should construct a fresh runner per temp.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from harness.runner import ModelRunner


@dataclass(frozen=True)
class PerturbationSeed:
    id: str
    canonical: str
    paraphrasings: tuple[str, ...]


def load_seeds(yaml_path: str | Path) -> list[PerturbationSeed]:
    data = yaml.safe_load(Path(yaml_path).read_text())
    out: list[PerturbationSeed] = []
    for s in data["seeds"]:
        out.append(
            PerturbationSeed(
                id=s["id"],
                canonical=s["canonical"],
                paraphrasings=tuple(s["paraphrasings"]),
            )
        )
    return out


_JUDGE_PROMPT = (
    "You are clustering responses by semantic equivalence. You will see {n} "
    "responses to a factual question. Label each response with an integer "
    "bucket id starting at 1 — responses with the same essential answer get "
    "the same id; responses with materially different answers get different "
    "ids. Reply with exactly {n} lines, each line in the form:\n\n"
    "  RESPONSE <index>: BUCKET <id>\n\n"
    "Where <index> matches the index of the response below (1-based).\n\n"
    "Underlying question (for context, do not score this): {question}\n\n"
    "Responses:\n"
    "{responses}\n"
)

_RE_BUCKET = re.compile(r"RESPONSE\s+(\d+)\s*:\s*BUCKET\s+(\d+)", re.IGNORECASE)


def _cluster_responses(judge: ModelRunner, question: str, responses: list[str]) -> list[int]:
    block = "\n\n".join(f"[Response {i + 1}] {r.strip()}" for i, r in enumerate(responses))
    prompt = _JUDGE_PROMPT.format(n=len(responses), question=question, responses=block)
    out = judge.complete(prompt)
    buckets: list[int | None] = [None] * len(responses)
    for m in _RE_BUCKET.finditer(out):
        idx = int(m.group(1)) - 1
        bid = int(m.group(2))
        if 0 <= idx < len(responses):
            buckets[idx] = bid
    # Fallback: any unparsed response gets its own bucket (worst-case for consistency)
    next_bucket = max([b for b in buckets if b is not None], default=0) + 1
    out_buckets: list[int] = []
    for b in buckets:
        if b is None:
            out_buckets.append(next_bucket)
            next_bucket += 1
        else:
            out_buckets.append(b)
    return out_buckets


def _max_bucket_fraction(buckets: list[int]) -> float:
    if not buckets:
        return 0.0
    counts: dict[int, int] = {}
    for b in buckets:
        counts[b] = counts.get(b, 0) + 1
    return max(counts.values()) / len(buckets)


def _mean_to_raw(score: float, cuts: list[dict[str, float]]) -> float:
    for cut in cuts:
        if score >= cut["min"]:
            return float(cut["raw"])
    return 0.0


DEFAULT_TEMPERATURES: tuple[float, ...] = (0.0, 0.3, 0.7)
DEFAULT_CUTS: list[dict[str, float]] = [
    {"min": 0.90, "raw": 4.0},
    {"min": 0.75, "raw": 3.0},
    {"min": 0.60, "raw": 2.0},
    {"min": 0.45, "raw": 1.0},
    {"min": 0.00, "raw": 0.0},
]


def run(
    runner_factory: Callable[[float], ModelRunner],
    judge: ModelRunner,
    seeds: list[PerturbationSeed],
    *,
    temperatures: tuple[float, ...] = DEFAULT_TEMPERATURES,
    cuts: list[dict[str, float]] | None = None,
    on_progress: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """For each seed, run 5 paraphrasings × len(temperatures) temps. Cluster, score.

    Returns dict with dim_id=P1.3, mean_consistency, raw, and trace.
    """
    cuts = list(cuts) if cuts is not None else DEFAULT_CUTS
    trace: list[dict[str, Any]] = []
    consistencies: list[float] = []
    n_variants = 0
    # Pre-build runners per temperature so factory is called once per temp, not per call
    runners: dict[float, ModelRunner] = {t: runner_factory(t) for t in temperatures}
    for idx, seed in enumerate(seeds, 1):
        responses: list[str] = []
        variants: list[tuple[str, float]] = []
        for paraphrase in seed.paraphrasings:
            for t in temperatures:
                variants.append((paraphrase, t))
        for paraphrase, t in variants:
            r = runners[t].complete(paraphrase)
            responses.append(r)
        n_variants = len(variants)
        buckets = _cluster_responses(judge, seed.canonical, responses)
        cons = _max_bucket_fraction(buckets)
        consistencies.append(cons)
        record = {
            "id": seed.id,
            "canonical": seed.canonical,
            "n_variants": len(variants),
            "buckets": buckets,
            "consistency": cons,
            "variant_responses": [
                {"paraphrase": p, "temperature": t, "response": r}
                for (p, t), r in zip(variants, responses)
            ],
        }
        trace.append(record)
        if on_progress is not None:
            on_progress(idx, len(seeds), record)
    mean_cons = sum(consistencies) / len(consistencies) if consistencies else 0.0
    # judge model_id may not match subject; pick the first runner's id for the report
    subject_id = next(iter(runners.values())).model_id() if runners else "n/a"
    return {
        "dim_id": "P1.3",
        "benchmark": "perturbation_seeds-v0.1.0",
        "model_id": subject_id,
        "judge_id": judge.model_id(),
        "n_seeds": len(seeds),
        "n_variants_per_seed": n_variants,
        "temperatures": list(temperatures),
        "mean_consistency": mean_cons,
        "raw": _mean_to_raw(mean_cons, cuts),
        "trace": trace,
        "provenance_label_template": "category-proxy:{base_model}",
    }
