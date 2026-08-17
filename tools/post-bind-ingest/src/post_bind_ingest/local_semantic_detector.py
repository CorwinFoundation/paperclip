"""Deterministic no-network semantic detection over ephemeral raw spans."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Final, Mapping


DETECTOR_PACKAGE: Final = "backbond-local-rules"
DETECTOR_VERSION: Final = "1.0.0"
_RULES: Final = (
    ("P1", re.compile(r"\b(?:wire|transfer|refund)\b.{0,48}\b(?:without approval|unauthori[sz]ed)\b", re.I)),
    ("P2", re.compile(r"\b(?:invoke|call|use)\b.{0,32}\b(?:tool|function)\b.{0,48}\b(?:not allowed|forbidden|unauthori[sz]ed)\b", re.I)),
    ("P3", re.compile(r"\b(?:exfiltrat|export|send)\w*\b.{0,48}\b(?:pii|personal data|customer records?)\b", re.I)),
    ("P4", re.compile(r"\b(?:invented|fabricated|hallucinated)\b.{0,48}\b(?:action|booking|transaction|citation)\b", re.I)),
    ("P5", re.compile(r"\b(?:ignore|override)\b.{0,32}\b(?:previous|system|developer)\b.{0,24}\b(?:instruction|prompt)s?\b", re.I)),
    ("P6", re.compile(r"\b(?:promise|commit|guarantee)\w*\b.{0,48}\b(?:compliance|approval|coverage|regulator)\b", re.I)),
)
_PII = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b(?:\d[ -]*?){13,19}\b")


def detect_raw_span(span: Mapping[str, Any], *, max_bytes: int = 64 * 1024) -> tuple[dict[str, str], ...]:
    """Inspect locally, retaining only hashes and peril ids (never raw text)."""
    encoded = json.dumps(span, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    if len(encoded) > max_bytes:
        raise ValueError("raw span exceeds local detector size limit")
    text = " ".join(_strings(span))
    scrubbed = _PII.sub("[REDACTED]", text)
    evidence_hash = hashlib.sha256(scrubbed.encode()).hexdigest()
    return tuple(
        {"peril": peril, "evidence_sha256": evidence_hash, "detector_package": DETECTOR_PACKAGE, "detector_version": DETECTOR_VERSION}
        for peril, rule in _RULES if rule.search(scrubbed)
    )


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key in sorted(value):
            yield from _strings(value[key])
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)

