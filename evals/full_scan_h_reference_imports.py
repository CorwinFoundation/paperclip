"""Shared contract for Full Scan H reference imports (BEAAA-39491).

Reference cases are report-only full-scan inputs.  They have one immutable
form, are deduplicated against both reference and BackBond-authored cases, and
must never enter Starter or underwriting score inputs.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

FORM_POLICY = "fixed"
FORM_COUNT = 1
NEAR_DUPLICATE_JACCARD = 0.6


@dataclass(frozen=True)
class ReferenceCase:
    source: str
    source_version: str
    source_case_id: str
    prompt: str
    attack_payload: str
    licence: str = "MIT"

    @property
    def stable_id(self) -> str:
        body = f"{self.source}\0{self.source_version}\0{self.source_case_id}"
        return "full-scan-h-" + hashlib.sha256(body.encode()).hexdigest()

    @property
    def comparison_text(self) -> str:
        """Represent the complete imported attack scenario for deduplication."""
        return f"{self.prompt}\n{self.attack_payload}"

    def registry_row(self) -> dict[str, object]:
        """Return the only registry shape permitted for a reference import."""
        return {
            "stable_id": self.stable_id,
            "source": self.source,
            "source_version": self.source_version,
            "source_case_id": self.source_case_id,
            "licence": self.licence,
            "prompt": self.prompt,
            "attack_payload": self.attack_payload,
            "form_policy": FORM_POLICY,
            "forms": [{"form_id": "fixed", "prompt": self.prompt}],
            "starter_eligible": False,
            "score_input": False,
            "underwriting_active": False,
            "priced_score_weight": 0.0,
            "disposition": "report-only",
        }


def _shingles(text: str, size: int = 3) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    words = re.sub(r"[^\w\s]", " ", normalized).split()
    if not words:
        return frozenset()
    if len(words) <= size:
        return frozenset({" ".join(words)})
    return frozenset(" ".join(words[i : i + size]) for i in range(len(words) - size + 1))


def jaccard(left: str, right: str) -> float:
    a, b = _shingles(left), _shingles(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def deduplicate(
    imported: Iterable[ReferenceCase], backbond_prompts: Iterable[tuple[str, str]] = ()
) -> tuple[list[ReferenceCase], list[dict[str, object]]]:
    """Keep first canonical row; drop later prompt pairs with Jaccard > 0.6."""
    kept: list[ReferenceCase] = []
    dropped: list[dict[str, object]] = []
    canon = [(case_id, prompt, False) for case_id, prompt in backbond_prompts]
    for case in imported:
        match = next(
            ((case_id, score) for case_id, prompt, is_import in canon
             if (score := jaccard(
                 case.comparison_text if is_import else case.prompt, prompt
             )) > NEAR_DUPLICATE_JACCARD),
            None,
        )
        if match:
            dropped.append({"stable_id": case.stable_id, "duplicate_of": match[0],
                            "jaccard": round(match[1], 6)})
            continue
        kept.append(case)
        canon.append((case.stable_id, case.comparison_text, True))
    return kept, dropped
