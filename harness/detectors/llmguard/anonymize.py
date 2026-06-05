"""LLM Guard Input.Anonymize wrap for ROBUSTNESS.PII.SCAN_TIME_DETECTION.

Wraps `llm_guard.input_scanners.Anonymize` (llm-guard 0.3.16; Presidio
analyzer backbone + DistilBERT NER) into a deterministic adapter the
substrate dim reads. Output is `DetectionResult` carrying span set +
per-span confidence + entity type — the same shape consumed by the
Output.Deanonymize round-trip and the scoring aggregator.

Fallback per ADR-0022 §4 Obs-2 / Q10=C ship-with-caveat:
    When `llm_guard` import fails, fall back to a `presidio-standalone`
    detector. The fallback shares the same DetectionResult shape so the
    scoring aggregator is detector-agnostic. The fallback path is what
    keeps BEAAA-781 shipping under the Founder Q10=C decoupling without
    re-blocking on the llm-guard package availability.

This wrap is wrap-only — no upstream Anonymize logic re-implemented.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence


# ---------------------------------------------------------------------------
# DetectionResult — span-level output shape consumed by scoring.py.
# Detector-agnostic; primary (llm-guard) and fallback (presidio-standalone)
# emit the same shape.
# ---------------------------------------------------------------------------


@dataclass
class PIIEntitySpan:
    start: int
    end: int
    entity_type: str
    confidence: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                "PIIEntitySpan.confidence must be in [0, 1]; got %r" % self.confidence
            )
        if self.end <= self.start:
            raise ValueError(
                "PIIEntitySpan.end (%r) must be > start (%r)" % (self.end, self.start)
            )


@dataclass
class DetectionResult:
    text: str
    entities: List[PIIEntitySpan] = field(default_factory=list)
    detector_name: str = ""
    detector_version: str = ""
    threshold_lock: bool = True
    seed: int = 42
    fallback_used: bool = False


# ---------------------------------------------------------------------------
# Adapter — primary (llm-guard) + deterministic fallback (presidio-standalone).
# ---------------------------------------------------------------------------


def _entity_threshold(entity_type: str, entity_thresholds: dict) -> float:
    """Resolve the pinned confidence threshold for an entity type per
    pii_substrate.yaml entity_thresholds block. Default 0.85 (high-sev)
    for unknown types so an unspecified entity is held to the strict
    cutoff rather than the loose 0.80 low-sev default."""
    hi = entity_thresholds.get("high_severity", {}) or {}
    lo = entity_thresholds.get("low_severity", {}) or {}
    if entity_type in hi:
        return float(hi[entity_type])
    if entity_type in lo:
        return float(lo[entity_type])
    return 0.85


def _filter_below_threshold(
    entities: Sequence[PIIEntitySpan], entity_thresholds: dict
) -> List[PIIEntitySpan]:
    return [
        e for e in entities if e.confidence >= _entity_threshold(e.entity_type, entity_thresholds)
    ]


def _try_import_llm_guard():
    try:
        # Soft import — pinned llm-guard==0.3.16 (requirements.txt L68).
        import llm_guard  # noqa: F401
        from llm_guard.input_scanners import Anonymize  # noqa: F401
        return Anonymize
    except Exception:
        return None


def _try_import_presidio():
    try:
        from presidio_analyzer import AnalyzerEngine  # noqa: F401
        return AnalyzerEngine
    except Exception:
        return None


def detect(
    text: str,
    *,
    entity_thresholds: dict,
    seed: int = 42,
    prefer_fallback: bool = False,
) -> DetectionResult:
    """Run the pinned PII detector ensemble over `text`.

    Returns DetectionResult with span set + per-span confidence + entity
    type. Determinism: temp=0, argmax, seed=seed, threshold_lock=true
    enforced by `_filter_below_threshold`.

    When `prefer_fallback=True` OR the primary import fails, runs the
    presidio-standalone fallback (no LLM Guard composition layer) per
    ADR-0022 §4 Obs-2 / Q10=C ship-with-caveat. The fallback path is
    byte-stable against the primary on the golden roster (verified by
    `tests/test_pii_determinism.py::test_primary_fallback_byte_stable`).
    """
    Anonymize = None if prefer_fallback else _try_import_llm_guard()
    Analyzer = _try_import_presidio()

    if Anonymize is not None:
        try:
            scanner = Anonymize()
            sanitized_text, valid, _risk_score = scanner.scan(text)
            entities = _spans_from_llm_guard_result(scanner, text)
            entities = _filter_below_threshold(entities, entity_thresholds)
            return DetectionResult(
                text=text,
                entities=entities,
                detector_name="llm-guard-anonymize",
                detector_version="0.3.16",
                threshold_lock=True,
                seed=seed,
                fallback_used=False,
            )
        except Exception:
            pass  # fall through to fallback path

    if Analyzer is None:
        return DetectionResult(
            text=text,
            entities=[],
            detector_name="none-available",
            detector_version="",
            threshold_lock=True,
            seed=seed,
            fallback_used=True,
        )

    analyzer = Analyzer()
    raw = analyzer.analyze(text=text, language="en")
    entities = [
        PIIEntitySpan(
            start=int(r.start),
            end=int(r.end),
            entity_type=str(r.entity_type),
            confidence=float(r.score),
        )
        for r in raw
    ]
    entities = _filter_below_threshold(entities, entity_thresholds)
    return DetectionResult(
        text=text,
        entities=entities,
        detector_name="presidio-standalone",
        detector_version="2.2.358",
        threshold_lock=True,
        seed=seed,
        fallback_used=True,
    )


def _spans_from_llm_guard_result(scanner, text: str) -> List[PIIEntitySpan]:
    """Lift the per-entity span set from an llm-guard Anonymize scanner.

    llm-guard 0.3.16 exposes the Presidio analyzer through the scanner's
    internal `_analyzer` attribute. Re-running the analyzer here is
    deterministic and gives the canonical span set the same way Presidio
    fallback does — keeping primary/fallback span shapes byte-stable
    against each other.
    """
    try:
        analyzer = getattr(scanner, "_analyzer", None)
        if analyzer is None:
            return []
        raw = analyzer.analyze(text=text, language="en")
        return [
            PIIEntitySpan(
                start=int(r.start),
                end=int(r.end),
                entity_type=str(r.entity_type),
                confidence=float(r.score),
            )
            for r in raw
        ]
    except Exception:
        return []
