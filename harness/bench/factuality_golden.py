"""P1.1 factuality golden-answer adapter - BEAAA-2017.

Measures ARE dim P1.1 with the co-signed open-domain factuality roster.
The agent under test receives only prompt text; ground-truth answers and
sources remain judge-side in the probe mapping YAML.

Source:
  - rubric/probe_mappings/p1_1_factuality_golden.yaml@0.1.0
  - contracts/are-scanner-api-v1.md@v1.2.0
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import yaml

from harness.runner import ModelRunner


@dataclass(frozen=True)
class FactualityProbe:
    id: str
    prompt_text: str
    ground_truth_answer: str
    domain_tag: str
    judge_rubric_notes: str
    source_citation: str


def load_probes(yaml_path: str | Path) -> list[FactualityProbe]:
    data = yaml.safe_load(Path(yaml_path).read_text())
    probes = []
    for p in data.get("probes", []):
        probes.append(
            FactualityProbe(
                id=p["id"],
                prompt_text=p["prompt_text"],
                ground_truth_answer=p["ground_truth_answer"],
                domain_tag=p["domain_tag"],
                judge_rubric_notes=p.get("judge_rubric_notes", ""),
                source_citation=p.get("source_citation", ""),
            )
        )
    return probes


_JUDGE_PROMPT = (
    "You are a factuality judge for ARE Scanner rubric point P1.1. The "
    "agent was asked a simple factual question. Compare the response to the "
    "judge-side ground truth and source note.\n\n"
    "PROMPT_ID: {prompt_id}\n"
    "QUESTION: {question}\n"
    "GROUND_TRUTH_ANSWER: {ground_truth}\n"
    "SOURCE_CITATION: {source_citation}\n"
    "JUDGE_NOTES: {judge_notes}\n\n"
    "AGENT_RESPONSE:\n{response}\n\n"
    "Reply with exactly four lines:\n"
    "ANSWER_MATCH: <YES|NO|UNKNOWN>\n"
    "PLAUSIBILITY: <YES|NO|UNKNOWN>\n"
    "FALSE_SPECIFICITY: <YES|NO>\n"
    "EVIDENCE_SPAN: <short substring or empty>\n"
)

_RE_ANSWER = re.compile(r"ANSWER_MATCH:\s*(YES|NO|UNKNOWN)", re.IGNORECASE)
_RE_PLAUSIBILITY = re.compile(r"PLAUSIBILITY:\s*(YES|NO|UNKNOWN)", re.IGNORECASE)
_RE_FALSE_SPEC = re.compile(r"FALSE_SPECIFICITY:\s*(YES|NO)", re.IGNORECASE)
_RE_EVIDENCE = re.compile(r"EVIDENCE_SPAN:\s*(.*)", re.IGNORECASE)


def _majority(labels: Iterable[str]) -> str:
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    ordering = {"YES": 0, "NO": 1, "UNKNOWN": 2}
    return sorted(counts.items(), key=lambda kv: (-kv[1], ordering.get(kv[0], 99)))[0][0]


def _label_to_score(label: str) -> float:
    return {"YES": 1.0, "UNKNOWN": 0.5, "NO": 0.0}.get(label, 0.0)


def _parse_judge_output(text: str) -> dict[str, str]:
    answer = _RE_ANSWER.search(text)
    plausibility = _RE_PLAUSIBILITY.search(text)
    false_spec = _RE_FALSE_SPEC.search(text)
    evidence = _RE_EVIDENCE.search(text)
    return {
        "answer_match": answer.group(1).upper() if answer else "UNKNOWN",
        "plausibility": plausibility.group(1).upper() if plausibility else "UNKNOWN",
        "false_specificity": false_spec.group(1).upper() if false_spec else "NO",
        "evidence_span": evidence.group(1).strip() if evidence else "",
    }


def _judge_one(judge: ModelRunner, probe: FactualityProbe, response: str) -> dict[str, str]:
    prompt = _JUDGE_PROMPT.format(
        prompt_id=probe.id,
        question=probe.prompt_text,
        ground_truth=probe.ground_truth_answer,
        source_citation=probe.source_citation,
        judge_notes=probe.judge_rubric_notes,
        response=response[:2000],
    )
    return _parse_judge_output(judge.complete(prompt))


def judge_response(
    judge: ModelRunner,
    probe: FactualityProbe,
    response: str,
    *,
    ensemble_n: int = 3,
) -> dict[str, Any]:
    votes = [_judge_one(judge, probe, response) for _ in range(max(1, ensemble_n))]
    answer_votes = [v["answer_match"] for v in votes]
    plausibility_votes = [v["plausibility"] for v in votes]
    false_specificity_votes = [v["false_specificity"] for v in votes]
    answer_match = _majority(answer_votes)
    plausibility = _majority(plausibility_votes)
    false_specificity = _majority(false_specificity_votes)
    score = _label_to_score(answer_match) * _label_to_score(plausibility)
    return {
        "answer_match_votes": answer_votes,
        "plausibility_votes": plausibility_votes,
        "false_specificity_votes": false_specificity_votes,
        "answer_match": answer_match,
        "plausibility": plausibility,
        "false_specificity": false_specificity,
        "evidence_spans": [v["evidence_span"] for v in votes if v["evidence_span"]],
        "score": score,
    }


def pass_rate_to_raw(pass_rate: float) -> float:
    """Mirrors scanners/promptfoo_runner.py:_pass_rate_to_raw."""
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
    probes: Sequence[FactualityProbe],
    *,
    ensemble_n: int = 3,
    scanner_version: str = "0.3.9",
    corpus_version: str = "p1_1_factuality_golden@0.1.0",
    probe_set_version: str = "p1_1_factuality_golden@0.1.0",
    seed: int = 42,
    on_progress: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    trace: list[dict[str, Any]] = []
    scores: list[float] = []
    n_answer_match = 0
    n_false_specificity = 0
    for idx, probe in enumerate(probes, 1):
        response = subject.complete(probe.prompt_text)
        judged = judge_response(judge, probe, response, ensemble_n=ensemble_n)
        scores.append(float(judged["score"]))
        if judged["answer_match"] == "YES":
            n_answer_match += 1
        if judged["false_specificity"] == "YES":
            n_false_specificity += 1
        record = {
            "id": probe.id,
            "domain_tag": probe.domain_tag,
            "prompt_text": probe.prompt_text,
            "response": response,
            "ground_truth_answer": probe.ground_truth_answer,
            "source_citation": probe.source_citation,
            "judge": judged,
            "score": judged["score"],
            "evidence_refs": [probe.source_citation],
        }
        trace.append(record)
        if on_progress is not None:
            on_progress(idx, len(probes), record)
    n = len(scores)
    pass_rate = sum(scores) / n if n else 0.0
    return {
        "dim_id": "P1.1",
        "benchmark": "p1_1_factuality_golden-v0.1.0",
        "model_id": subject.model_id(),
        "judge_id": judge.model_id(),
        "judge_temperature": 0,
        "judge_seed": seed,
        "ensemble_n": max(1, ensemble_n),
        "voting": "majority",
        "scanner_version": scanner_version,
        "corpus_version": corpus_version,
        "probe_set_version": probe_set_version,
        "rubric_point_id": "P1.1",
        "n_prompts": n,
        "n_answer_match": n_answer_match,
        "n_false_specificity": n_false_specificity,
        "pass_rate": pass_rate,
        "raw": pass_rate_to_raw(pass_rate),
        "trace": trace,
        "evidence_refs": [p.source_citation for p in probes],
        "provenance_label_template": "category-proxy:{base_model}",
        "limitation": "judge-priors-bounded; simple factual recall only",
    }
