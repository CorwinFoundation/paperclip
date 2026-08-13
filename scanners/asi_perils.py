"""Closed OWASP Agentic Security Initiative peril-family identifiers.

The enum is reporting metadata for probe-family inventory. It does not enter
the Scanner API envelope or scoring path.
"""
from __future__ import annotations

from enum import Enum


class ASIPeril(str, Enum):
    ASI01 = "ASI01"
    ASI02 = "ASI02"
    ASI03 = "ASI03"
    ASI04 = "ASI04"
    ASI05 = "ASI05"
    ASI06 = "ASI06"
    ASI07 = "ASI07"
    ASI08 = "ASI08"
    ASI09 = "ASI09"
    ASI10 = "ASI10"


__all__ = ["ASIPeril"]
