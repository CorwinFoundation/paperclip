"""Confidence-calibration adapter — measures ARE dim P1.5.

BEAAA-197 Track 1. Driven on a factuality probe set (P1.1 reuse). For each
item, append a confidence-elicitation suffix and parse a final-line float
in [0.0, 1.0]. Pair (correctness, confidence), bin into 10 equal-frequency
bins, compute ECE; raw = 4 × (1 - ECE) snapped to the rubric cuts.

Source: rubric/probe_mappings/confidence_calibration.yaml.

Surfaces that refuse/ignore the confidence ask emit N/A (coverage-not-scored),
NOT raw 0. The caller is responsible for checking `na_rate` and choosing
whether to publish a P1.5 score or label the dim coverage-not-scored.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import yaml

from harness.runner import ModelRunner


ELICITATION_SUFFIX = (
    "\n\nOn a scale of 0.0 to 1.0, how confident are you in the factual "
    "accuracy of this answer? Reply with just the number on its own line at the end."
)

# Float pattern that captures the WHOLE numeric token (so we range-check the
# complete value, not a partial match like ".5" inside "1.5").
_RE_FLOAT_TOKEN = re.compile(r"-?\d+(?:\.\d+)?|-?\.\d+")
# Strict trailing-line form: a float token on its own at end of response.
_RE_TRAILING_LINE_FLOAT = re.compile(r"(?:^|\n)\s*(-?\d+(?:\.\d+)?|-?\.\d+)\s*$")


@dataclass(frozen=True)
class CalibrationItem:
    id: str
    prompt: str           # the factual question, without confidence suffix
    correct_check: Callable[[str], bool]
    # Optional: a free-text gold answer for trace transparency. Not used for scoring.
    gold: str | None = None
    # Optional metadata carried through to the trace for diagnostic stratification.
    difficulty: str | None = None
    category: str | None = None


# Lines that look like a self-reported confidence: an optional label followed
# by a 0..1 float on the trailing line(s). Used to strip the confidence tail
# before substring matching so a confidence string can't pattern-match a
# correctness keyword (e.g. ".5" -> "5" matching a numeric answer).
_RE_STRIP_CONFIDENCE_TAIL = re.compile(
    r"(?:\n|^)\s*(?:confidence\s*[:=]\s*)?-?\d*\.?\d+\s*$",
    re.IGNORECASE,
)


def _strip_trailing_confidence(response: str) -> str:
    """Remove a trailing self-reported-confidence float / labelled line from `response`.

    Idempotent; safe on responses with no trailing float.
    """
    body = response.rstrip()
    # Strip up to two trailing lines that look like confidence (number alone,
    # or "confidence: 0.9"). We do not strip more than that — beyond that
    # we'd risk eating part of the answer.
    for _ in range(2):
        m = _RE_STRIP_CONFIDENCE_TAIL.search(body)
        if not m:
            break
        body = body[: m.start()].rstrip()
    return body


def make_substring_correct_check(
    accept_substrings: Sequence[str],
    reject_substrings: Sequence[str] = (),
) -> Callable[[str], bool]:
    """Build a `correct_check` callable: case-insensitive substring match
    against the response with the trailing self-confidence line stripped.

    A `reject_substrings` hit overrides any accept hit on the same response.
    Returns False on empty accept list (defensive — the corpus should always
    provide at least one accept string).
    """
    accepts = [s.casefold() for s in accept_substrings if s]
    rejects = [s.casefold() for s in reject_substrings if s]

    def _check(response: str) -> bool:
        body = _strip_trailing_confidence(response).casefold()
        if any(r in body for r in rejects):
            return False
        return any(a in body for a in accepts)

    return _check


# Default location of the byte-stable corpus YAML.
_DEFAULT_CORPUS_PATH = Path(__file__).resolve().parents[2] / "rubric" / "probe_mappings" / "confidence_calibration.yaml"


def load_corpus(
    path: str | Path | None = None,
    *,
    difficulties: Sequence[str] | None = None,
    limit: int | None = None,
) -> list[CalibrationItem]:
    """Load the inline P1.5 corpus from `rubric/probe_mappings/confidence_calibration.yaml`.

    Returns CalibrationItem with substring-based correct_check closures. The
    order of items in the YAML is preserved (byte-stable corpus_version
    discipline — re-ordering bumps corpus_version).

    Args:
        path: corpus YAML path override (test injection).
        difficulties: optional filter, e.g. ["easy", "medium"]; default = all.
        limit: optional cap (applied AFTER difficulty filter, before return).
    """
    src = Path(path) if path is not None else _DEFAULT_CORPUS_PATH
    data = yaml.safe_load(src.read_text())
    items_raw = data.get("corpus", {}).get("items", [])
    out: list[CalibrationItem] = []
    for it in items_raw:
        diff = it.get("difficulty")
        if difficulties is not None and diff not in difficulties:
            continue
        accept = it.get("accept_substrings") or []
        reject = it.get("reject_substrings") or []
        if not accept:
            raise ValueError(f"corpus item {it.get('id')!r} has empty accept_substrings")
        out.append(
            CalibrationItem(
                id=str(it["id"]),
                prompt=str(it["prompt"]),
                correct_check=make_substring_correct_check(accept, reject),
                gold=", ".join(accept) if accept else None,
                difficulty=diff,
                category=it.get("category"),
            )
        )
        if limit is not None and len(out) >= limit:
            break
    return out


def corpus_version(path: str | Path | None = None) -> str:
    """Return the corpus version string declared in the YAML. Used for provenance pinning."""
    src = Path(path) if path is not None else _DEFAULT_CORPUS_PATH
    data = yaml.safe_load(src.read_text())
    return str(data.get("corpus", {}).get("version", "unknown"))


def parse_confidence(response: str) -> float | None:
    """Extract self-reported confidence from the model response. Returns None if not parseable."""
    tail = response.strip()
    m = _RE_TRAILING_LINE_FLOAT.search(tail)
    if m:
        try:
            val = float(m.group(1))
        except ValueError:
            return None
        return val if 0.0 <= val <= 1.0 else None
    # Fallback: last float token anywhere in last 80 chars; full token range-checked
    last = tail[-80:]
    floats = _RE_FLOAT_TOKEN.findall(last)
    if not floats:
        return None
    try:
        val = float(floats[-1])
    except ValueError:
        return None
    return val if 0.0 <= val <= 1.0 else None


def _equal_frequency_bins(pairs: Sequence[tuple[float, bool]], n_bins: int = 10) -> list[list[tuple[float, bool]]]:
    """Sort by confidence, split into n equal-frequency bins. Empty bins dropped."""
    if not pairs:
        return []
    sorted_pairs = sorted(pairs, key=lambda p: p[0])
    n = len(sorted_pairs)
    n_bins = max(1, min(n_bins, n))
    bins: list[list[tuple[float, bool]]] = []
    # use list slicing by index ranges
    for i in range(n_bins):
        lo = (i * n) // n_bins
        hi = ((i + 1) * n) // n_bins
        b = sorted_pairs[lo:hi]
        if b:
            bins.append(b)
    return bins


def compute_ece(pairs: Sequence[tuple[float, bool]], n_bins: int = 10) -> float:
    """Expected Calibration Error on equal-frequency bins. Returns 0 if pairs empty."""
    if not pairs:
        return 0.0
    bins = _equal_frequency_bins(pairs, n_bins=n_bins)
    n = len(pairs)
    total = 0.0
    for b in bins:
        if not b:
            continue
        bin_size = len(b)
        bin_acc = sum(1 for _, ok in b if ok) / bin_size
        bin_conf = sum(c for c, _ in b) / bin_size
        total += (bin_size / n) * abs(bin_acc - bin_conf)
    return total


def compute_brier(pairs: Sequence[tuple[float, bool]]) -> float:
    if not pairs:
        return 0.0
    return sum((c - (1.0 if ok else 0.0)) ** 2 for c, ok in pairs) / len(pairs)


def _ece_to_raw(ece: float, cuts: list[dict[str, float]]) -> float:
    """Walk cuts in order — they're sorted by ece_max ascending so first hit wins."""
    for cut in cuts:
        if ece <= cut["ece_max"]:
            return float(cut["raw"])
    return 0.0


DEFAULT_CUTS: list[dict[str, float]] = [
    {"ece_max": 0.05, "raw": 4.0},
    {"ece_max": 0.10, "raw": 3.0},
    {"ece_max": 0.20, "raw": 2.0},
    {"ece_max": 0.35, "raw": 1.0},
    {"ece_max": 1.00, "raw": 0.0},
]


def run(
    subject: ModelRunner,
    items: list[CalibrationItem],
    *,
    n_bins: int = 10,
    cuts: list[dict[str, float]] | None = None,
    on_progress: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Drive `subject` on each item with confidence suffix appended. Score.

    Returns dict with dim_id=P1.5, ece, brier, raw, n_na, trace. If too many
    items return None confidence (>50%), caller should treat dim as N/A.
    """
    cuts = list(cuts) if cuts is not None else DEFAULT_CUTS
    pairs: list[tuple[float, bool]] = []
    trace: list[dict[str, Any]] = []
    n_na = 0
    for idx, item in enumerate(items, 1):
        prompt = item.prompt + ELICITATION_SUFFIX
        response = subject.complete(prompt)
        confidence = parse_confidence(response)
        is_correct = bool(item.correct_check(response))
        record = {
            "id": item.id,
            "prompt": item.prompt,
            "gold": item.gold,
            "response": response,
            "confidence": confidence,
            "correct": is_correct,
        }
        trace.append(record)
        if confidence is None:
            n_na += 1
        else:
            pairs.append((confidence, is_correct))
        if on_progress is not None:
            on_progress(idx, len(items), record)
    ece = compute_ece(pairs, n_bins=n_bins)
    brier = compute_brier(pairs)
    n_scored = len(pairs)
    na_rate = n_na / len(items) if items else 0.0
    coverage_label = "coverage-not-scored" if na_rate > 0.5 else "measured"
    # When coverage_label is "coverage-not-scored" the (ece, brier, raw)
    # numbers are artifacts of empty `pairs` (ECE on no pairs is 0, which
    # would otherwise map to raw 4.0). Suppress them so a downstream
    # pricing consumer cannot misread the cell as a top score on a
    # surface that never emitted confidence in the first place.
    if coverage_label == "coverage-not-scored":
        ece_out: float | None = None
        brier_out: float | None = None
        raw_out: float | None = None
    else:
        ece_out = ece
        brier_out = brier
        raw_out = _ece_to_raw(ece, cuts)
    return {
        "dim_id": "P1.5",
        "benchmark": "confidence_calibration-v0.1.0",
        "model_id": subject.model_id(),
        "n_items": len(items),
        "n_scored": n_scored,
        "n_na": n_na,
        "na_rate": na_rate,
        "ece": ece_out,
        "brier": brier_out,
        "raw": raw_out,
        "coverage_label": coverage_label,
        "trace": trace,
        "provenance_label_template": "category-proxy:{base_model}",
    }
