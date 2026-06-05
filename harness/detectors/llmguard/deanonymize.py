"""LLM Guard Output.Deanonymize round-trip — pure-function, no ML.

Restores anonymized placeholders back to original PII spans for
evidence-trail completeness. Maintains a deterministic placeholder map
keyed by entity-type-index so multiple-occurrence-of-same-PII surfaces
get the same placeholder shape.

No upstream coupling — the Output.Deanonymize side is byte-stable
mechanical replacement; if llm-guard is unavailable, the local
implementation is exact-equivalent (placeholder map is the only state).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from harness.detectors.llmguard.anonymize import DetectionResult, PIIEntitySpan


@dataclass
class AnonymizedDoc:
    anonymized_text: str
    placeholder_map: Dict[str, str] = field(default_factory=dict)
    original_entities: List[PIIEntitySpan] = field(default_factory=list)


def _placeholder_for(entity_type: str, index: int) -> str:
    return "[%s_%d]" % (entity_type.upper(), index)


def anonymize_text(detection: DetectionResult) -> AnonymizedDoc:
    """Replace each detected entity span in the original text with a
    deterministic placeholder. Same entity_type → same numeric index
    per first-appearance order; identical text spans collapse to the
    same placeholder for round-trip stability."""
    entities_sorted = sorted(detection.entities, key=lambda e: e.start)
    placeholder_map: Dict[str, str] = {}
    type_index: Dict[str, int] = {}
    text_to_placeholder: Dict[Tuple[str, str], str] = {}

    pieces: List[str] = []
    cursor = 0
    for ent in entities_sorted:
        original = detection.text[ent.start:ent.end]
        key = (ent.entity_type, original)
        ph = text_to_placeholder.get(key)
        if ph is None:
            type_index[ent.entity_type] = type_index.get(ent.entity_type, -1) + 1
            ph = _placeholder_for(ent.entity_type, type_index[ent.entity_type])
            text_to_placeholder[key] = ph
            placeholder_map[ph] = original
        pieces.append(detection.text[cursor:ent.start])
        pieces.append(ph)
        cursor = ent.end
    pieces.append(detection.text[cursor:])
    return AnonymizedDoc(
        anonymized_text="".join(pieces),
        placeholder_map=placeholder_map,
        original_entities=list(entities_sorted),
    )


def deanonymize_text(anonymized: AnonymizedDoc) -> str:
    """Reverse the placeholder substitution. Exact inverse of
    `anonymize_text` when the placeholder map is intact."""
    out = anonymized.anonymized_text
    # Longest placeholder first so a shorter placeholder doesn't shadow
    # a longer one that happens to share a prefix (defensive; placeholders
    # have stable [TYPE_n] shape so this is belt-and-suspenders).
    for ph in sorted(anonymized.placeholder_map.keys(), key=len, reverse=True):
        out = out.replace(ph, anonymized.placeholder_map[ph])
    return out


def round_trip_byte_identical(detection: DetectionResult) -> bool:
    """Property-test helper: anonymize → deanonymize must reproduce the
    original text byte-for-byte. Used by `tests/test_pii_determinism.py`.
    """
    return deanonymize_text(anonymize_text(detection)) == detection.text
