"""BMI band assignment — 12-band (A/B/C/D × ±) v1.0 FROZEN 2026-04-30.

Source authority: BEAAA-57 band-cuts-spec v1.0 + pricing-v1 §3 (BEAAA-10 rev 2).

Letter cuts drive coverage triggers. Modifier cuts are soft within-letter pricing
signals. No coverage trigger fires on a modifier boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Letter cut points (lower bound, upper bound inclusive).
_LETTER_CUTS = [
    ("A", 85, 100, "Preferred"),
    ("B", 70, 84, "Standard"),
    ("C", 55, 69, "Surplus (remediation endorsement)"),
    ("D", 0, 54, "Decline / layered-excess only"),
]

# Modifier cuts: (letter, modifier, lo, hi, factor).
_MODIFIER_CUTS = [
    ("A", "+", 95, 100, 0.93),
    ("A", "neutral", 90, 94, 1.00),
    ("A", "-", 85, 89, 1.07),
    ("B", "+", 80, 84, 0.93),
    ("B", "neutral", 75, 79, 1.00),
    ("B", "-", 70, 74, 1.07),
    ("C", "+", 65, 69, 0.93),
    ("C", "neutral", 60, 64, 1.00),
    ("C", "-", 55, 59, 1.07),
    ("D", "+", 40, 54, 0.93),
    ("D", "neutral", 20, 39, 1.00),
    ("D", "-", 0, 19, 1.10),
]

BANDS_VERSION = "1.0"
BANDS_FROZEN_AT = "2026-04-30"


class BandError(ValueError):
    pass


@dataclass(frozen=True)
class Band:
    letter: str
    modifier: str
    modifier_factor: float
    posture: str

    def display(self) -> str:
        if self.modifier == "neutral":
            return self.letter
        return f"{self.letter}{self.modifier}"


def assign_band(bmi: int) -> Band:
    """Map a 0–100 BMI integer to its 12-band assignment."""
    if not isinstance(bmi, int):
        raise BandError(f"bmi must be int, got {type(bmi).__name__}")
    if not 0 <= bmi <= 100:
        raise BandError(f"bmi {bmi} out of [0,100]")

    letter, posture = _letter_for(bmi)
    modifier, factor = _modifier_for(letter, bmi)
    return Band(letter=letter, modifier=modifier, modifier_factor=factor, posture=posture)


def _letter_for(bmi: int) -> tuple[str, str]:
    for letter, lo, hi, posture in _LETTER_CUTS:
        if lo <= bmi <= hi:
            return letter, posture
    raise BandError(f"no letter for bmi {bmi}")


def _modifier_for(letter: str, bmi: int) -> tuple[str, float]:
    for cut_letter, modifier, lo, hi, factor in _MODIFIER_CUTS:
        if cut_letter == letter and lo <= bmi <= hi:
            return modifier, factor
    raise BandError(f"no modifier for letter {letter} bmi {bmi}")


def band_to_dict(band: Band) -> dict[str, Any]:
    return {
        "letter": band.letter,
        "modifier": band.modifier,
        "modifier_factor": band.modifier_factor,
        "display": band.display(),
        "posture": band.posture,
        "bands_version": BANDS_VERSION,
    }
