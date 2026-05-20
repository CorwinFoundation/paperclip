"""Plain-language layer for the daily Founder PDF digest.

ADR-0014 (BEAAA-597 comment) is canonical for this map. The renderer applies
gloss on first occurrence per page; subsequent same-page mentions stay
glossed or revert to the short form. Canonical state in the Paperclip API and
audit trail is unchanged — this layer is presentation-only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape


@dataclass(frozen=True)
class Term:
    canonical: str
    gloss: str
    short_after_first_use: str
    pattern: re.Pattern[str]


def _term(canonical: str, gloss: str, short_after: str | None = None) -> Term:
    pat = re.compile(rf"\b{re.escape(canonical)}\b")
    return Term(canonical, gloss, short_after or canonical, pat)


TERMS: tuple[Term, ...] = (
    _term("ARE Scanner", "our agent risk scanner (ARE Scanner)", "the scanner"),
    _term("ARE", "our agent risk score (ARE)", "the score"),
    _term("IFA", "us (IFA)", "us"),
    _term("Phase 1", "current 14-day plan", "the 14-day plan"),
    _term("request_confirmation", "your decision needed", "your decision needed"),
    _term("ask_user_questions", "questions waiting for your answer",
          "questions waiting for your answer"),
    _term("in_review", "waiting on you", "waiting on you"),
    _term("blocked", "stuck", "stuck"),
    _term("in_progress", "in flight", "in flight"),
    _term("backlog", "queued", "queued"),
    _term("done", "completed", "completed"),
)


TICKET_ID_PATTERN = re.compile(r"\bBEAAA-\d+\b")


_FOOTER_INDEX_SENTINEL = "\u0000BEAAA\u0000"


_STRANDED_PREPOSITION = re.compile(
    r"\b(?:per|via|see|for|cf\.?|ref\.?|tracked in|filed in|under)\s*(?=[.,;:!?)]|$)",
    re.IGNORECASE,
)


def strip_ticket_ids(text: str) -> tuple[str, list[str]]:
    """Pull all BEAAA-N ids out of body copy and return (clean_text, ids).

    Order of first appearance is preserved (deduplicated). Also drops
    leftover "per", "via", "see" prepositions that introduced the
    removed id, and tidies stray punctuation / empty parens.
    """
    seen: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        tid = match.group(0)
        if tid not in seen:
            seen.append(tid)
        return ""

    cleaned = TICKET_ID_PATTERN.sub(_replace, text)
    cleaned = _STRANDED_PREPOSITION.sub("", cleaned)
    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r"\[\s*\]", "", cleaned)
    cleaned = re.sub(r"\(\s+", "(", cleaned)
    cleaned = re.sub(r"\s+\)", ")", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+—\s+(?=[.,;:!?)]|$)", "", cleaned)
    cleaned = re.sub(r",\s*(?=[.,;:!?)]|$)", "", cleaned)
    return cleaned.strip(), seen


_COMBINED_TERM_PATTERN = re.compile(
    "|".join(
        rf"(?P<t{i}>\b{re.escape(t.canonical)}\b)"
        for i, t in enumerate(sorted(TERMS, key=lambda x: -len(x.canonical)))
    )
)
_TERMS_BY_LENGTH = sorted(TERMS, key=lambda x: -len(x.canonical))


class PageGlosser:
    """Applies first-use gloss per page. Reset per page.

    Implementation note: a single combined regex with longest-first
    alternation is used so that overlapping terms (e.g. "ARE Scanner" vs
    "ARE") cannot recursively re-match inside an already-substituted
    gloss. `re.sub` does not re-scan substituted text, so one pass is
    deterministic.
    """

    def __init__(self) -> None:
        self._glossed_this_page: set[str] = set()

    def reset(self) -> None:
        self._glossed_this_page.clear()

    def apply(self, text: str) -> str:
        def _sub(match: re.Match[str]) -> str:
            for i, t in enumerate(_TERMS_BY_LENGTH):
                if match.group(f"t{i}") is not None:
                    if t.canonical not in self._glossed_this_page:
                        self._glossed_this_page.add(t.canonical)
                        return t.gloss
                    return t.short_after_first_use
            return match.group(0)
        return _COMBINED_TERM_PATTERN.sub(_sub, text)


ANTI_PATTERN_PHRASES: tuple[str, ...] = (
    "in today's",
    "empowering",
    "game-changing",
    "revolutionary",
    "next-gen",
    "cutting-edge",
    "paradigm shift",
    "trusted partner",
    "comprehensive solution",
    "end-to-end platform",
    "best-in-class",
    "responsible ai",
    "scalable governance",
    "ai-powered insights",
    "best practices",
    "unlocking value",
    "driving outcomes",
    "delivering impact",
    "imagine if",
    "picture this",
    "whether you are",
    "guarantee",
)


_DEPRECATED_PATTERNS = (
    re.compile(r"\bBMI\b"),
    re.compile(r"\bARI\b"),
    re.compile(r"\bBEAI Model Protector\b", re.IGNORECASE),
    re.compile(r"\bBEAI\b"),
    re.compile(r"\bBEA\b"),
)


_DEPRECATED_SUBSTITUTIONS = (
    (re.compile(r"\bBEAI Model Protector\b", re.IGNORECASE), "ARE Scanner"),
    (re.compile(r"\bBEAI Maturity Index\b", re.IGNORECASE), "ARE"),
    (re.compile(r"\bAgent Readiness Index\b", re.IGNORECASE), "ARE"),
    (re.compile(r"\bBMI\b"), "ARE"),
    (re.compile(r"\bARI\b"), "ARE"),
    (re.compile(r"\bBEAI\b"), "IFA"),
    (re.compile(r"\bBEA\b"), "IFA"),
)


def canonicalize(text: str) -> str:
    """Replace deprecated codenames with canonical terms before gloss layer.

    Source data sometimes carries legacy codenames (BMI / BEAI / etc.) — per
    glossary.md they are deprecated, replace before this hits the page.
    """
    for pat, repl in _DEPRECATED_SUBSTITUTIONS:
        text = pat.sub(repl, text)
    return text


def find_anti_patterns(body_text: str) -> list[str]:
    """Return any anti-pattern phrases found in body text (case-insensitive)."""
    lower = body_text.lower()
    return [p for p in ANTI_PATTERN_PHRASES if p in lower]


def find_deprecated_terms(body_text: str) -> list[str]:
    return [p.pattern for p in _DEPRECATED_PATTERNS if p.search(body_text)]


def find_raw_canonicals(body_text: str) -> list[str]:
    """Return canonical terms still present un-glossed (every match counts)."""
    hits: list[str] = []
    for term in TERMS:
        for m in term.pattern.finditer(body_text):
            hits.append(m.group(0))
    return hits


def html_safe_gloss(text: str, glosser: PageGlosser) -> str:
    """Escape user text, then apply gloss. Order matters: escape first."""
    return glosser.apply(escape(text))
