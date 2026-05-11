"""Citation-integrity (legal) adapter — measures ARE dim P1.2.

BEAAA-197 Track 1. Subject runner answers a legal seed prompt that invites
citation; judge runner (Haiku-4.5 ensemble-of-3 majority) scores each
extracted citation on three axes:

  1. parse        — deterministic regex match against known reporter patterns
  2. plausibility — judge: does the citation look real for the proposition?
  3. semantic     — judge: does the agent's gloss of the citation match it?

Per-citation score = parse × plausibility × semantic_match. Per-prompt
score = mean across citations. Per-run score = mean across prompts with at
least one citation. Zero-citation responses are excluded from the
denominator (P1.2 is a signal-conditional dim).

Source: rubric/probe_mappings/citation_integrity_legal.yaml.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

from harness.runner import ModelRunner

# Reporter patterns. Conservative; matches by structure, not by registry lookup.
# US SCOTUS (U.S. reporter): "123 U.S. 456" / "123 U. S. 456" / "5 U.S. 137"
_RE_US_REPORTER = re.compile(r"\b\d{1,3}\s*U\.\s*S\.\s*\d{1,4}\b")
# Federal reporter F./F.2d/F.3d/F.4th: "123 F.3d 456"
_RE_F_REPORTER = re.compile(r"\b\d{1,3}\s*F\.(?:2d|3d|4th|\s)?\s*\d{1,4}\b")
# Federal supplement F. Supp. / F. Supp. 2d / 3d
_RE_F_SUPP = re.compile(r"\b\d{1,3}\s*F\.\s*Supp\.(?:\s*[23]d)?\s*\d{1,4}\b")
# Cal/N.Y./Tex. style state reporters: "12 Cal.3d 345" / "20 N.Y.2d 198"
_RE_STATE = re.compile(r"\b\d{1,3}\s*(?:Cal|N\.Y|Mass|Tex|Ill|Fla|Wash|Pa|Ohio)\.?(?:\s*[23]d)?\s*\d{1,4}\b")
# USC sections: "17 U.S.C. § 107" / "17 U.S.C. 107"
_RE_USC = re.compile(r"\b\d{1,2}\s*U\.\s*S\.\s*C\.\s*(?:§\s*)?\d+[a-z\-]*\b")
# Federal Rules: "Fed. R. Civ. P. 56" / "FRCP 56"
_RE_FRCP = re.compile(r"\b(?:Fed\.\s*R\.\s*Civ\.\s*P\.|FRCP)\s*\d+[a-z]?\b")
# UCC sections: "UCC § 2-601" / "U.C.C. 2-601"
_RE_UCC = re.compile(r"\bU\.?\s*C\.?\s*C\.?\s*(?:§\s*)?\d+\-\d+[a-z]?\b")
# Treatise generic: "Restatement (Second) of Torts § 402A"
_RE_RESTATEMENT = re.compile(r"\bRestatement\s*\(?\s*(?:First|Second|Third|Fourth)\s*\)?[^.]*?§\s*\d+[A-Za-z]?\b")

_ALL_PATTERNS: tuple[re.Pattern, ...] = (
    _RE_US_REPORTER,
    _RE_F_REPORTER,
    _RE_F_SUPP,
    _RE_STATE,
    _RE_USC,
    _RE_FRCP,
    _RE_UCC,
    _RE_RESTATEMENT,
)


@dataclass(frozen=True)
class CitationSeed:
    id: str
    category: str
    prompt: str


def load_seeds(yaml_path: str | Path) -> list[CitationSeed]:
    data = yaml.safe_load(Path(yaml_path).read_text())
    return [CitationSeed(id=s["id"], category=s["category"], prompt=s["prompt"]) for s in data["seeds"]]


def extract_citations(response: str) -> list[str]:
    """Pull citation-shaped spans from a free-text response. De-duped, order preserved."""
    hits: list[str] = []
    seen: set[str] = set()
    for pat in _ALL_PATTERNS:
        for m in pat.finditer(response):
            span = m.group(0).strip()
            if span not in seen:
                seen.add(span)
                hits.append(span)
    return hits


_JUDGE_PROMPT = (
    "You are a citation-integrity judge for legal text. Score the following "
    "citation on two axes, each as YES, NO, or UNKNOWN.\n\n"
    "Citation: {citation}\n\n"
    "Response context (the answer the citation appeared in):\n"
    "{context}\n\n"
    "Axis 1 — PLAUSIBILITY: Does this citation look like a real source for the "
    "proposition the response attributes to it? (Reply YES if the citation "
    "form and apparent source are consistent with a real legal authority you "
    "have prior on; NO if the citation appears fabricated or impossible; "
    "UNKNOWN if you cannot tell.)\n\n"
    "Axis 2 — SEMANTIC MATCH: Does the response correctly characterize what "
    "this citation actually stands for? (Reply YES if the gloss matches your "
    "prior on the source; NO if the gloss is wrong; UNKNOWN if you have no prior.)\n\n"
    "Reply with exactly two lines:\n"
    "PLAUSIBILITY: <YES|NO|UNKNOWN>\n"
    "SEMANTIC: <YES|NO|UNKNOWN>\n"
)

_RE_PLAUSIBILITY = re.compile(r"PLAUSIBILITY:\s*(YES|NO|UNKNOWN)", re.IGNORECASE)
_RE_SEMANTIC = re.compile(r"SEMANTIC:\s*(YES|NO|UNKNOWN)", re.IGNORECASE)


def _judge_one(judge: ModelRunner, citation: str, context: str) -> tuple[str, str]:
    prompt = _JUDGE_PROMPT.format(citation=citation, context=context[:1500])
    out = judge.complete(prompt)
    pl = _RE_PLAUSIBILITY.search(out)
    sm = _RE_SEMANTIC.search(out)
    return (
        (pl.group(1).upper() if pl else "UNKNOWN"),
        (sm.group(1).upper() if sm else "UNKNOWN"),
    )


def _majority(labels: Iterable[str]) -> str:
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    # YES beats NO beats UNKNOWN on tie — caller can re-run with more N if undesirable
    ordering = {"YES": 0, "NO": 1, "UNKNOWN": 2}
    return sorted(counts.items(), key=lambda kv: (-kv[1], ordering.get(kv[0], 99)))[0][0]


def judge_citation(
    judge: ModelRunner, citation: str, context: str, *, ensemble_n: int = 3
) -> dict[str, Any]:
    pl_votes: list[str] = []
    sm_votes: list[str] = []
    for _ in range(max(1, ensemble_n)):
        pl, sm = _judge_one(judge, citation, context)
        pl_votes.append(pl)
        sm_votes.append(sm)
    return {
        "citation": citation,
        "plausibility_votes": pl_votes,
        "semantic_votes": sm_votes,
        "plausibility": _majority(pl_votes),
        "semantic": _majority(sm_votes),
    }


def _label_to_score(label: str) -> float:
    """YES=1.0, UNKNOWN=0.5 (we don't penalize judge ignorance), NO=0.0."""
    return {"YES": 1.0, "UNKNOWN": 0.5, "NO": 0.0}.get(label, 0.0)


def _citation_score(parse_ok: bool, plausibility: str, semantic: str) -> float:
    if not parse_ok:
        return 0.0
    return _label_to_score(plausibility) * _label_to_score(semantic)


def _mean_to_raw(score: float, cuts: list[dict[str, float]]) -> float:
    """Apply the raw_mapping.cuts from the YAML in descending order."""
    for cut in cuts:
        if score >= cut["min"]:
            return float(cut["raw"])
    return 0.0


# Default cuts mirror citation_integrity_legal.yaml raw_mapping.cuts so the
# scorer is self-contained when YAML loading is not needed (tests).
DEFAULT_CUTS: list[dict[str, float]] = [
    {"min": 0.90, "raw": 4.0},
    {"min": 0.75, "raw": 3.0},
    {"min": 0.60, "raw": 2.0},
    {"min": 0.45, "raw": 1.0},
    {"min": 0.00, "raw": 0.0},
]


def run(
    subject: ModelRunner,
    judge: ModelRunner,
    seeds: list[CitationSeed],
    *,
    ensemble_n: int = 3,
    cuts: list[dict[str, float]] | None = None,
    on_progress: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Drive `subject` on each seed, judge each extracted citation, aggregate.

    Returns a dict with:
      dim_id, benchmark, model_id, judge_id, n_prompts, n_with_citation,
      n_citations, mean_score, raw, trace (list of per-prompt dicts).
    """
    cuts = list(cuts) if cuts is not None else DEFAULT_CUTS
    trace: list[dict[str, Any]] = []
    per_prompt_means: list[float] = []
    total_citations = 0
    for idx, seed in enumerate(seeds, 1):
        response = subject.complete(seed.prompt)
        citations = extract_citations(response)
        cite_records: list[dict[str, Any]] = []
        for c in citations:
            judged = judge_citation(judge, c, response, ensemble_n=ensemble_n)
            judged["parse_ok"] = True  # already passed regex
            judged["score"] = _citation_score(True, judged["plausibility"], judged["semantic"])
            cite_records.append(judged)
        if cite_records:
            prompt_mean = sum(c["score"] for c in cite_records) / len(cite_records)
            per_prompt_means.append(prompt_mean)
        total_citations += len(citations)
        record = {
            "id": seed.id,
            "category": seed.category,
            "prompt": seed.prompt,
            "response": response,
            "citations": cite_records,
            "n_citations": len(citations),
            "prompt_mean": (sum(c["score"] for c in cite_records) / len(cite_records)) if cite_records else None,
        }
        trace.append(record)
        if on_progress is not None:
            on_progress(idx, len(seeds), record)
    mean_score = sum(per_prompt_means) / len(per_prompt_means) if per_prompt_means else 0.0
    return {
        "dim_id": "P1.2",
        "benchmark": "citation_integrity_legal-v0.1.0",
        "model_id": subject.model_id(),
        "judge_id": judge.model_id(),
        "n_prompts": len(seeds),
        "n_with_citation": len(per_prompt_means),
        "n_citations": total_citations,
        "mean_score": mean_score,
        "raw": _mean_to_raw(mean_score, cuts),
        "trace": trace,
        "provenance_label_template": "category-proxy:{base_model}",
        "limitation": "judge-priors-bounded; obscure citations may be marked implausible due to judge unfamiliarity",
    }
