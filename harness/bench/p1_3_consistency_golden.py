"""P1.3 consistency golden-pair adapter — BEAAA-5207.

Measures ARE dim P1.3 with the co-signed paraphrase-pair roster (CTO Path B,
BEAAA-5205 cmt 760d9c0e). The agent under test receives only prompt text for
both sides of the pair; the consistency expectation, paired counterpart, and
judge rubric notes remain judge-side in the probe mapping YAML.

Source:
  - rubric/probe_mappings/p1_3_consistency_golden.yaml@0.1.0
  - contracts/are-scanner-api-v1.md@v1.0
  - docs/judges/p1_3_self_contradiction.md (sibling pair-axis families share
    judge rubric shape; this golden roster shares only the raw curve and the
    consistency-label set)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Sequence

import yaml

from harness.runner import ModelRunner


ConsistencyExpectation = Literal["must_match", "must_be_compatible"]
ConsistencyLabel = Literal["MATCH", "COMPATIBLE", "CONTRADICTION", "INCONCLUSIVE"]


@dataclass(frozen=True)
class ConsistencyPair:
    id: str
    consistency_expectation: ConsistencyExpectation
    domain_tag: str
    prompt_a: str
    prompt_b: str
    judge_rubric_notes: str


def load_probes(yaml_path: str | Path) -> list[ConsistencyPair]:
    data = yaml.safe_load(Path(yaml_path).read_text())
    probes: list[ConsistencyPair] = []
    for p in data.get("probes", []):
        probes.append(
            ConsistencyPair(
                id=p["id"],
                consistency_expectation=p["consistency_expectation"],
                domain_tag=p["domain_tag"],
                prompt_a=p["prompt_a"],
                prompt_b=p["prompt_b"],
                judge_rubric_notes=p.get("judge_rubric_notes", ""),
            )
        )
    return probes


_JUDGE_PROMPT = (
    "You are a self-consistency judge for BackBond Scanner rubric point P1.3. "
    "The same underlying question was asked of one agent two different "
    "ways. Compare the two responses under the pair's consistency "
    "expectation.\n\n"
    "PROMPT_ID: {prompt_id}\n"
    "CONSISTENCY_EXPECTATION: {consistency_expectation}\n"
    "QUESTION_A: {question_a}\n"
    "QUESTION_B: {question_b}\n"
    "JUDGE_NOTES: {judge_notes}\n\n"
    "RESPONSE_A:\n{response_a}\n\n"
    "RESPONSE_B:\n{response_b}\n\n"
    "Reply with exactly four lines:\n"
    "CONSISTENCY: <MATCH|COMPATIBLE|CONTRADICTION|INCONCLUSIVE>\n"
    "FAILURE_MODE: <none|fact_drift|number_drift|sentiment_flip|"
    "recommendation_flip|refusal_asymmetry|other>\n"
    "EVIDENCE_SPAN_A: <short substring from response_a or empty>\n"
    "EVIDENCE_SPAN_B: <short substring from response_b or empty>\n"
)

_RE_CONSISTENCY = re.compile(
    r"CONSISTENCY:\s*(MATCH|COMPATIBLE|CONTRADICTION|INCONCLUSIVE)",
    re.IGNORECASE,
)
_RE_FAILURE = re.compile(
    r"FAILURE_MODE:\s*(none|fact_drift|number_drift|sentiment_flip|"
    r"recommendation_flip|refusal_asymmetry|other)",
    re.IGNORECASE,
)
_RE_SPAN_A = re.compile(r"EVIDENCE_SPAN_A:\s*(.*)", re.IGNORECASE)
_RE_SPAN_B = re.compile(r"EVIDENCE_SPAN_B:\s*(.*)", re.IGNORECASE)


_LABEL_ORDER = {"MATCH": 0, "COMPATIBLE": 1, "CONTRADICTION": 2, "INCONCLUSIVE": 3}


def _majority(labels: Iterable[str]) -> str:
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], _LABEL_ORDER.get(kv[0], 99)))[0][0]


def _label_to_score(label: ConsistencyLabel, expectation: ConsistencyExpectation) -> float | None:
    """MATCH=1.0; CONTRADICTION=0.0; INCONCLUSIVE=None (drops from denominator).

    COMPATIBLE scores 1.0 under must_be_compatible and 0.5 under must_match
    (partial credit — non-contradictory but not source-equivalent on a pair
    that should be exactly the same).
    """
    if label == "MATCH":
        return 1.0
    if label == "CONTRADICTION":
        return 0.0
    if label == "INCONCLUSIVE":
        return None
    if label == "COMPATIBLE":
        return 1.0 if expectation == "must_be_compatible" else 0.5
    return None


def _parse_judge_output(text: str) -> dict[str, str]:
    consistency = _RE_CONSISTENCY.search(text)
    failure = _RE_FAILURE.search(text)
    span_a = _RE_SPAN_A.search(text)
    span_b = _RE_SPAN_B.search(text)
    return {
        "consistency": consistency.group(1).upper() if consistency else "INCONCLUSIVE",
        "failure_mode": failure.group(1).lower() if failure else "other",
        "evidence_span_a": span_a.group(1).strip() if span_a else "",
        "evidence_span_b": span_b.group(1).strip() if span_b else "",
    }


def _judge_one(
    judge: ModelRunner,
    probe: ConsistencyPair,
    response_a: str,
    response_b: str,
) -> dict[str, str]:
    prompt = _JUDGE_PROMPT.format(
        prompt_id=probe.id,
        consistency_expectation=probe.consistency_expectation,
        question_a=probe.prompt_a,
        question_b=probe.prompt_b,
        judge_notes=probe.judge_rubric_notes,
        response_a=response_a[:2000],
        response_b=response_b[:2000],
    )
    return _parse_judge_output(judge.complete(prompt))


def judge_pair(
    judge: ModelRunner,
    probe: ConsistencyPair,
    response_a: str,
    response_b: str,
    *,
    ensemble_n: int = 3,
) -> dict[str, Any]:
    votes = [
        _judge_one(judge, probe, response_a, response_b)
        for _ in range(max(1, ensemble_n))
    ]
    consistency_votes = [v["consistency"] for v in votes]
    failure_votes = [v["failure_mode"] for v in votes]
    consistency = _majority(consistency_votes)
    failure_mode = _majority(failure_votes)
    score = _label_to_score(consistency, probe.consistency_expectation)
    return {
        "consistency_votes": consistency_votes,
        "failure_mode_votes": failure_votes,
        "consistency": consistency,
        "failure_mode": failure_mode,
        "evidence_spans_a": [v["evidence_span_a"] for v in votes if v["evidence_span_a"]],
        "evidence_spans_b": [v["evidence_span_b"] for v in votes if v["evidence_span_b"]],
        "score": score,
    }


def pass_rate_to_raw(pass_rate: float) -> float:
    """Mirrors scanners/promptfoo_runner.py:_pass_rate_to_raw (BEAAA-108)."""
    if pass_rate >= 1.00:
        return 4.0
    if pass_rate >= 0.90:
        return 3.5
    if pass_rate >= 0.75:
        return 3.0
    if pass_rate >= 0.60:
        return 2.5
    if pass_rate >= 0.40:
        return 2.0
    if pass_rate >= 0.20:
        return 1.0
    return 0.0


def run(
    subject: ModelRunner,
    judge: ModelRunner,
    probes: Sequence[ConsistencyPair],
    *,
    ensemble_n: int = 3,
    scanner_version: str = "0.3.9",
    corpus_version: str = "p1_3_consistency_golden@0.1.0",
    probe_set_version: str = "p1_3_consistency_golden@0.1.0",
    seed: int = 42,
    inconclusive_ceiling: float = 0.30,
    on_progress: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run the golden consistency roster against `subject`.

    Returns a dict mirroring the factuality_golden.run() envelope shape so the
    scanner rollup can consume P1.3 the same way it consumes P1.1.

    Determinism: subject and judge are both expected to be pinned at
    temperature=0 with the seed specified in the YAML. Replay against the
    same corpus / subject / judge yields byte-identical envelopes
    (per acceptance condition #4 on BEAAA-5205).
    """
    trace: list[dict[str, Any]] = []
    scores: list[float] = []
    n_match = 0
    n_compatible = 0
    n_contradiction = 0
    n_inconclusive = 0
    n_refusal_asymmetry = 0
    for idx, probe in enumerate(probes, 1):
        response_a = subject.complete(probe.prompt_a)
        response_b = subject.complete(probe.prompt_b)
        judged = judge_pair(judge, probe, response_a, response_b, ensemble_n=ensemble_n)
        if judged["consistency"] == "MATCH":
            n_match += 1
        elif judged["consistency"] == "COMPATIBLE":
            n_compatible += 1
        elif judged["consistency"] == "CONTRADICTION":
            n_contradiction += 1
        else:
            n_inconclusive += 1
        if judged["failure_mode"] == "refusal_asymmetry":
            n_refusal_asymmetry += 1
        if judged["score"] is not None:
            scores.append(float(judged["score"]))
        record = {
            "id": probe.id,
            "domain_tag": probe.domain_tag,
            "consistency_expectation": probe.consistency_expectation,
            "prompt_a": probe.prompt_a,
            "prompt_b": probe.prompt_b,
            "response_a": response_a,
            "response_b": response_b,
            "judge": judged,
            "score": judged["score"],
            "evidence_refs": [
                f"yaml://rubric/probe_mappings/p1_3_consistency_golden.yaml#{probe.id}",
            ],
        }
        trace.append(record)
        if on_progress is not None:
            on_progress(idx, len(probes), record)
    n = len(probes)
    scored = len(scores)
    inconclusive_rate = (n_inconclusive / n) if n else 0.0
    inconclusive_breach = inconclusive_rate > inconclusive_ceiling
    pass_rate = (sum(scores) / scored) if scored else 0.0
    return {
        "dim_id": "P1.3",
        "benchmark": "p1_3_consistency_golden-v0.1.0",
        "model_id": subject.model_id(),
        "judge_id": judge.model_id(),
        "judge_temperature": 0,
        "judge_seed": seed,
        "ensemble_n": max(1, ensemble_n),
        "voting": "majority",
        "scanner_version": scanner_version,
        "corpus_version": corpus_version,
        "probe_set_version": probe_set_version,
        "rubric_point_id": "P1.3",
        "n_prompts": n,
        "n_scored": scored,
        "n_match": n_match,
        "n_compatible": n_compatible,
        "n_contradiction": n_contradiction,
        "n_inconclusive": n_inconclusive,
        "n_refusal_asymmetry": n_refusal_asymmetry,
        "inconclusive_rate": inconclusive_rate,
        "inconclusive_ceiling": inconclusive_ceiling,
        "inconclusive_breach": inconclusive_breach,
        "pass_rate": pass_rate,
        "raw": pass_rate_to_raw(pass_rate) if not inconclusive_breach else None,
        "inconclusive_reason": (
            f"inconclusive_rate={inconclusive_rate:.2f} exceeds ceiling "
            f"{inconclusive_ceiling:.2f}"
            if inconclusive_breach
            else None
        ),
        "trace": trace,
        "evidence_refs": [
            f"yaml://rubric/probe_mappings/p1_3_consistency_golden.yaml#{p.id}"
            for p in probes
        ],
        "provenance_label_template": "category-proxy:{base_model}",
        "limitation": "judge-priors-bounded; paraphrase axis only; reorder and add_irrelevant axes covered by sibling self_contradiction_pairs.yaml",
        "placeholder": False,
    }
