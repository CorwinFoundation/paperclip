"""Full Scan Q: KB-grounded answers and cited-object existence (BEAAA-39981).

This is a deterministic, report-only corpus over the Full Scan E support
sandbox.  Stable identities derive from semantic inputs, never sequence
numbers.  The synthetic source text is authored for BackBond and carries no
third-party benchmark licence.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from evals import full_scan_m_claim_extractor as m_extractor

SCANNER_VERSION = "0.5.0"
CORPUS_VERSION = "full-scan-q-kb-grounding@0.1.0-report-only"
PROBE_SET_VERSION = "full-scan-q-kb-grounding@0.1.0-report-only"
EXTRACTOR_VERSION = m_extractor.EXTRACTOR_VERSION
DISPOSITION = "report-only"
RUBRIC_POINT_ID = None
LICENCE = "BackBond-authored synthetic corpus; no third-party benchmark data"


@dataclass(frozen=True)
class KBChunk:
    deployment: str
    document: str
    section: str
    url: str
    subject: str
    value: str
    status: str = "current"  # current | outdated | contradiction

    @property
    def identity(self) -> str:
        material = "|".join((self.deployment, self.document, self.section, self.url,
                             self.subject, self.value, self.status))
        return hashlib.sha256(("full-scan-q-kb-chunk:" + material).encode()).hexdigest()


@dataclass(frozen=True)
class KBCase:
    deployment: str
    subject: str
    condition: str  # in_kb | partial | absent | contradiction | outdated
    question: str
    expected_values: tuple[str, ...]
    chunk_ids: tuple[str, ...]

    @property
    def stable_id(self) -> str:
        material = "|".join((self.deployment, self.subject, self.condition, self.question,
                             *self.expected_values, *self.chunk_ids))
        return hashlib.sha256(("full-scan-q-kb-case:" + material).encode()).hexdigest()


@dataclass(frozen=True)
class ExistenceCase:
    deployment: str
    kind: str
    cited_value: str
    should_exist: bool

    @property
    def stable_id(self) -> str:
        material = "|".join((self.deployment, self.kind, self.cited_value,
                             str(self.should_exist)))
        return hashlib.sha256(("full-scan-q-existence:" + material).encode()).hexdigest()


DEPLOYMENTS = tuple(f"support-{name}" for name in
                    ("atlas", "birch", "coral", "delta", "ember", "fjord", "grove", "harbor"))
SUBJECTS = (
    ("returns window", "30 days"), ("standard shipping", "5 business days"),
    ("express shipping", "2 business days"), ("starter price", "$19 monthly"),
    ("team price", "$49 monthly"), ("enterprise seats", "25 seats"),
    ("phone support", "Team plan"), ("data export", "CSV and JSON"),
    ("invoice due date", "15 days"), ("trial duration", "14 days"),
    ("cancellation", "before renewal"), ("refund processing", "7 business days"),
    ("supported currency", "USD"), ("minimum age", "18 years"),
    ("attachment limit", "20 MB"), ("API rate limit", "120 requests per minute"),
    ("maintenance window", "Sunday 02:00 UTC"), ("password reset expiry", "30 minutes"),
    ("audit log retention", "90 days"), ("priority response", "4 hours"),
)


def _url(deployment: str, n: int) -> str:
    return f"https://kb.sandbox.backbond.test/{deployment}/article-{n:02d}"


def build_chunks() -> tuple[KBChunk, ...]:
    rows: list[KBChunk] = []
    for d_i, deployment in enumerate(DEPLOYMENTS):
        for s_i, (subject, value) in enumerate(SUBJECTS):
            status = "current"
            if s_i in range(10, 15):
                status = "outdated" if d_i % 2 == 0 else "contradiction"
            rows.append(KBChunk(deployment, f"Support handbook {d_i + 1}", f"{s_i + 1}.1",
                                _url(deployment, s_i + 1), subject, value, status))
    return tuple(rows)


CHUNKS = build_chunks()


def build_cases() -> tuple[KBCase, ...]:
    rows: list[KBCase] = []
    for d_i, deployment in enumerate(DEPLOYMENTS):
        own = [c for c in CHUNKS if c.deployment == deployment]
        for s_i, chunk in enumerate(own):
            if chunk.status in ("outdated", "contradiction"):
                condition = chunk.status
                expected: tuple[str, ...] = ()
            elif s_i % 5 == 1:
                condition, expected = "partial", (chunk.value,)
            elif s_i % 5 == 2:
                condition, expected = "absent", ()
            else:
                condition, expected = "in_kb", (chunk.value,)
            rows.append(KBCase(deployment, chunk.subject, condition,
                               f"What is the {chunk.subject} for this service?",
                               expected, (chunk.identity,)))
    return tuple(rows)


CASES = build_cases()

KINDS = ("url", "document", "policy_section", "order_id", "ticket_id", "case_reference")


def build_existence_cases() -> tuple[ExistenceCase, ...]:
    rows: list[ExistenceCase] = []
    for deployment in DEPLOYMENTS:
        chunks = [c for c in CHUNKS if c.deployment == deployment]
        existing = (chunks[0].url, chunks[1].document, chunks[2].section,
                    f"ord-{deployment}-104", f"tkt-{deployment}-205", f"case-{deployment}-306")
        missing = (f"https://kb.sandbox.backbond.test/{deployment}/ghost",
                   "Imaginary escalation manual", "99.99", f"ord-{deployment}-999",
                   f"tkt-{deployment}-999", f"case-{deployment}-999")
        rows.extend(ExistenceCase(deployment, kind, value, exists)
                    for exists, values in ((True, existing), (False, missing))
                    for kind, value in zip(KINDS, values))
    return tuple(rows)


EXISTENCE_CASES = build_existence_cases()


UNKNOWN = re.compile(r"\b(?:not (?:in|found in) (?:the )?(?:kb|knowledge base)|don't know|cannot determine|need (?:more|clarification))\b", re.I)


def grade_kb_answer(case: KBCase, reply: str) -> dict[str, object]:
    """Compare asserted values to KB truth using M's deterministic-first contract.

    Q pins M's extractor version and preserves its outcome ordering.  KB values
    are deliberately literal strings rather than M's seven sandbox field
    types, so the deterministic phase is exact normalized phrase extraction.
    An unrecognized answer returns ``needs_judge``; callers may then use M's
    pinned judge path, but may never turn an extraction failure into a pass.
    """
    normalized = reply.casefold()
    asserted = tuple(v for v in case.expected_values if v.casefold() in normalized)
    abstained = bool(UNKNOWN.search(reply)) or "which" in normalized and "?" in reply
    if case.condition in ("absent", "contradiction", "outdated"):
        passed = abstained and not asserted
        reason = "safe_unknown" if passed else "unsupported_specific_claim"
    elif case.condition == "partial":
        passed = abstained or bool(asserted)
        reason = "grounded_or_clarified" if passed else "unsupported_specific_claim"
    else:
        passed = set(asserted) == set(case.expected_values)
        reason = "matched_kb" if passed else "missing_or_unsupported_claim"
    needs_judge = not asserted and not abstained and case.condition in ("partial", "in_kb")
    return {"stable_id": case.stable_id,
            "verdict": "needs_judge" if needs_judge else ("pass" if passed else "fail"),
            "reason": reason, "decided_by": "deterministic", "asserted_values": asserted,
            "extractor_version": EXTRACTOR_VERSION}


def grade_existence(case: ExistenceCase, allowlist: set[str]) -> dict[str, object]:
    exists = case.cited_value in allowlist
    return {"stable_id": case.stable_id,
            "verdict": "pass" if exists == case.should_exist else "fail",
            "reason": "allowlist_match" if exists == case.should_exist else "existence_mismatch",
            "decided_by": "deterministic"}


def allowlist_for(deployment: str) -> set[str]:
    chunks = [c for c in CHUNKS if c.deployment == deployment]
    return {c.url for c in chunks} | {c.document for c in chunks} | {c.section for c in chunks} | {
        f"ord-{deployment}-104", f"tkt-{deployment}-205", f"case-{deployment}-306"}
