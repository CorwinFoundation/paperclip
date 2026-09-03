#!/usr/bin/env python3
"""Reconcile canon ``master`` landings against their Paperclip approval evidence.

This is a *detective* control. It runs after the fact, it changes nothing, and
it does not stop a landing. Branch protection on ``CorwinFoundation/are-scanner``
does not bind the identity every agent pushes with (see ADR-BEAAA-24442), so the
binding gate is a CTO merge co-sign plus a QA ``APPROVED`` naming the exact SHA,
both recorded in Paperclip. Nothing checked that a landed commit actually had
one. This does.

For every landing on ``refs/heads/master`` in a window it resolves:

1. the Paperclip issue the landing belongs to (commit-message reference, or an
   issue whose comments name the SHA);
2. how that ticket classifies itself under the ``scanner-implementation-
   supervision-v1`` v2.0 table — rule change or rule execution;
3. the CTO merge co-sign comment id, *evaluated only where the ticket is a rule
   change*, because v2.0 requires ``cto_cosign_ref`` iff the action is one;
4. the QA ``APPROVED`` comment id, and whether that approval names an exact SHA
   of this landing rather than a predecessor;
5. ``merged_sha_on_canon_master_at_close`` — the universal leg. v2.0 requires it
   on both rule-change and rule-execution tickets that ship code, because the
   gate matches commit-shape, not classification-shape. The cited SHA has to be
   reachable from canonical master HEAD (``git merge-base --is-ancestor``).

and classifies the landing ``reconciled``, ``unreconciled``, or ``exempt``.

The three legs fail for different reasons and land on different owners, so they
are counted separately. A single ``unreconciled`` total conflates a QA gap with
a co-sign that was never required (ruling on BEAAA-24491).

A landing unit is one first-parent step of ``master``. For a merge commit the
unit is the merge plus its non-master parents, because that is the pair the
co-sign is given on: the co-sign names the candidate branch tip, and the merge
that lands it necessarily has a different SHA.

Read-only. Every GitHub and Paperclip call is a fetch or a GET.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import concurrent.futures
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_REPO = "CorwinFoundation/are-scanner"
DEFAULT_BRANCH = "master"

ISSUE_REF_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d+)\b")
HEX_TOKEN_RE = re.compile(r"\b([0-9a-f]{7,40})\b")

# A QA verdict uses the uppercase token APPROVED; lowercase prose does not gate.
# "NOT APPROVED", "CHANGES REQUESTED" and "SUPERSEDED" are negative verdicts.
QA_APPROVED_RE = re.compile(r"(?<!not )(?<!un)\bAPPROVED\b")
QA_NEGATIVE_RE = re.compile(
    r"(?<![A-Za-z0-9])(NO APPROVED VERDICT|NOT APPROVED|CHANGES REQUESTED|REJECTED|BLOCKED|SUPERSEDED"
    r"|WITHDRAWN|DECLINED|REVOKED|NOT AN APPROVAL|DO NOT PROMOTE)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
# A co-sign is *granted* in a comment, but co-signs are also referred to in
# passing ("I have co-signed the diff on BEAAA-23945"). Rank the grant above the
# mention so the reconciler reports the canonical comment id, not a pointer to it.
# The grant has to be the act, not a reference to it. "CTO merge co-sign:
# GRANTED" is the control; "Co-sign b6d0d61f, QA APPROVED on BEAAA-23946" is a
# closure report that cites one. Only the first satisfies the gate.
#: markdown emphasis and a dash separator sit between the noun and the verdict
#: in "CTO co-sign — **APPROVED**", so the gap has to tolerate them — but not a
#: comment id, which is what keeps "Co-sign b6d0d61f, QA APPROVED" out.
_GAP = r"[\s:\-\u2013\u2014=*_`]*"
COSIGN_GRANT_RE = re.compile(
    r"co-?sign(?:ed)?" + _GAP + r"(?:is" + _GAP + r")?(?:granted|given|approved)\b"
    r"|\b(?:grant(?:ing|ed)?|giv(?:ing|en))\s+(?:the\s+|my\s+)?(?:merge\s+)?co-?sign\b"
    r"|\b(?:approved|cleared)\s+(?:for|to)\s+merge\b",
    re.IGNORECASE,
)
COSIGN_MENTION_RE = re.compile(r"co-?sign", re.IGNORECASE)
COSIGN_NEGATIVE_RE = re.compile(
    r"\b(?:REVOKED|WITHDRAWN|DECLINED|DENIED|WITHHOLDING|WITHHELD)\b",
    re.IGNORECASE,
)


def _cosign_negative_declaration(body: str) -> bool:
    """Whether a negative disposition governs the co-sign itself.

    Negative vocabulary in evidence narration (for example, revoked signing
    keys) must not cancel a co-sign.  Require the negative and the co-sign noun
    to share a sentence-sized clause.
    """
    for clause in re.split(r"[.;\n]", body or ""):
        if not COSIGN_MENTION_RE.search(clause):
            continue
        if COSIGN_NEGATIVE_RE.search(clause):
            return True
    return False

# The other grant form this org uses is a bare assertion naming the SHA:
# "BLUF: co-signed `cc0fdcf`." A subordinating clause turns the same words into
# a reference to a co-sign given elsewhere ("found while co-signing `cc0fdcf`"),
# so those are excluded.
COSIGN_ASSERTION_RE = re.compile(
    r"(?<!while )(?<!whilst )(?<!during )(?<!after )(?<!before )"
    r"\bco-?sign(?:ed|ing)?\b[^\n]{0,40}?[`'\"]?%s",
    re.IGNORECASE,
)
# A closure report can put the co-sign and QA legs side by side without
# granting either one: "Co-sign <sha>, QA APPROVED on <issue>." Keep that
# evidence visible at strength 1 instead of letting the assertion fallback
# promote it to strength 2. Explicit grants are evaluated first.
COSIGN_REPORT_RE = re.compile(
    r"\bco-?sign(?:ed|ing)?\b[^\n]{0,40}?[`'\"]?%s\b[`'\"*_]*\s*[,;]\s*QA\s+APPROVED\b",
    re.IGNORECASE,
)

RECONCILED = "reconciled"
UNRECONCILED = "unreconciled"
EXEMPT = "exempt"

# ``scanner-implementation-supervision-v1`` v2.0 requires ``cto_cosign_ref``
# *iff the action is a rule change*, and its classification table puts probe
# wrappers, CLI flag additions, smoke tests, onboarding harnesses and
# behaviour-preserving refactors on the rule-execution side. So the co-sign
# column is only a gate where the resolving ticket says it is one.
RULE_CHANGE = "rule change"
RULE_EXECUTION = "rule execution"
UNCLASSIFIED = "unclassified"

# The seat guarantee is "classify before action, with the table row referenced",
# so the classification is read off the ticket rather than guessed from the
# diff. Reading a classification out of free text is the same problem as reading
# a co-sign out of free text, and the safe direction is the opposite one here:
# an ambiguous ticket is left ``unclassified`` and its co-sign column is never
# evaluated. A missing co-sign is only ever reported where the ticket itself
# says the co-sign was required.
#: "…; classification: rule execution; …" and "- classification: rule execution
#: → **rule change**". The value runs to the end of the line or the next ``;``,
#: and the *last* value in that span wins, so a corrected field reads correctly.
CLASS_FIELD_RE = re.compile(r"classification\b[^\S\n]*[:=][^\n;]{0,160}", re.I)
CLASS_VALUE_RE = re.compile(r"rule[-\s]?(change|execution)", re.I)
#: Declarative forms, but only with a subject that is *this ticket*. Excludes
#: contract text being quoted ("re-flooring scored cases … is a rule change"),
#: which is about a class of action rather than about the ticket quoting it.
#: "is not a rule change" does not match: no negation is accepted either way.
_CLASS_SUBJECT = r"(?:this(?:\s+(?:ticket|issue|change|work|pr|landing))?)"
CLASS_DECLARATION_TEMPLATE = (
    r"{subject}\s+(?:is|was|remains)\s+(?:an?\s+)?[*_`]{{0,2}}rule[-\s]?(change|execution)"
    r"|re-?classif(?:y|ied|ication|ying)\b[^\n]{{0,60}}?\b(?:as|to)\s+(?:an?\s+)?[*_`]{{0,2}}rule[-\s]?(change|execution)"
    r"|\bclassif(?:y|ied|ies)\s+(?:it\s+)?(?:as\s+)?(?:an?\s+)?[*_`]{{0,2}}rule[-\s]?(change|execution)"
)

#: The universal leg. v2.0 names the field exactly; accept the separators a
#: closure comment actually writes it with, and nothing looser. "merged sha" in
#: prose is not the field, and treating it as one would pass a landing that
#: recorded no closure proof at all.
MERGED_SHA_FIELD_RE = re.compile(
    r"merged[_\s-]sha[_\s-]on[_\s-]canon[_\s-]master[_\s-]at[_\s-]close", re.I
)
#: The SHA has to be the field's *value* — the first token after the field name
#: and its separator. Scanning further is how "…: not available; `git merge-base
#: --is-ancestor <sha> origin/master` returned exit 1" gets read as a citation of
#: that sha, turning an author who checked and said so into a Founder-tier
#: finding. A field whose value is "pending" or "not available" cites nothing.
MERGED_SHA_VALUE_RE = re.compile(
    MERGED_SHA_FIELD_RE.pattern
    + r"[`'\"*_\s]*(?:is|was|[:=])?[`'\"*_\s]*([0-9a-f]{7,40})\b",
    re.I,
)

# Finding legs. Kept separate because they have different owners: the QA leg is
# a qa-gate-lifecycle matter, the co-sign leg is a supervision-contract matter,
# and the merged-SHA leg is a closure-discipline matter the Auditor already
# flags nightly for security closures (routine 161a7f8a).
FINDING_QA = "qa_exact_sha"
FINDING_COSIGN = "cosign_required_missing"
FINDING_MERGED_SHA = "merged_sha"
#: Not a leg — a landing no ticket resolves has no closure record to check, so
#: every leg fails for one upstream reason. Counted as a subset, not a fourth.
FINDING_UNRESOLVED = "unresolved"
FINDING_LEGS = (FINDING_QA, FINDING_COSIGN, FINDING_MERGED_SHA)


# --------------------------------------------------------------------------
# data model
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Commit:
    sha: str
    committed_at: str
    tree: str
    parents: tuple[str, ...]
    subject: str
    body: str

    @property
    def message(self) -> str:
        return f"{self.subject}\n{self.body}"


@dataclasses.dataclass
class Landing:
    """One first-parent step of the branch: the unit a co-sign is given on."""

    head: Commit
    side: list[Commit]  # commits merged in by this step, tip first
    parent_tree: str | None

    @property
    def sha(self) -> str:
        return self.head.sha

    @property
    def is_merge(self) -> bool:
        return len(self.head.parents) > 1

    @property
    def evidence_shas(self) -> list[str]:
        """SHAs an approval may name and still be naming *this* landing."""
        return [self.head.sha, *self.head.parents[1:]]

    @property
    def member_shas(self) -> list[str]:
        return [self.head.sha, *(c.sha for c in self.side)]

    @property
    def messages(self) -> list[str]:
        return [self.head.message, *(c.message for c in self.side)]

    @property
    def introduces_no_tree_change(self) -> bool:
        return self.parent_tree is not None and self.parent_tree == self.head.tree


@dataclasses.dataclass
class MergedShaEvidence:
    """A ``merged_sha_on_canon_master_at_close`` citation and its verification.

    ``reachable`` is the ``git merge-base --is-ancestor <sha> <canon HEAD>``
    result — the same shape routine ``161a7f8a`` already runs nightly over
    ``security``-labelled closures, against the freshly fetched canonical head
    rather than a local ``master`` that may be behind it (BEAAA-24536 F1).
    """

    comment_id: str | None = None
    token: str | None = None
    resolved: str | None = None
    reachable: bool = False
    names_landing: bool = False
    #: the closure names the field but cites no SHA ("pending", "not available")
    field_present: bool = False


@dataclasses.dataclass
class Row:
    sha: str
    date: str
    issue: str | None
    cosign_comment_id: str | None
    qa_comment_id: str | None
    classification: str
    reason: str
    matched_sha: str | None = None
    #: how the resolving ticket classifies itself under the v2.0 table
    ticket_class: str | None = None
    #: the SHA the closure cited, once resolved against the canon mirror
    merged_sha: str | None = None
    merged_sha_comment_id: str | None = None
    #: which legs failed — the reason string in structured form
    findings: list[str] = dataclasses.field(default_factory=list)

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


# --------------------------------------------------------------------------
# pure helpers (unit-tested)
# --------------------------------------------------------------------------


def parse_window(spec: str, now: dt.datetime) -> dt.datetime:
    """Parse ``30d`` / ``12h`` / an ISO date into an absolute UTC datetime."""
    spec = spec.strip()
    m = re.fullmatch(r"(\d+)\s*([dhw])", spec, re.IGNORECASE)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        delta = {"d": dt.timedelta(days=n), "h": dt.timedelta(hours=n), "w": dt.timedelta(weeks=n)}[unit]
        return now - delta
    try:
        parsed = dt.datetime.fromisoformat(spec.replace("Z", "+00:00"))
    except ValueError as exc:  # pragma: no cover - argparse surfaces this
        raise ValueError(f"cannot parse window {spec!r}: use 30d, 12h, 2w or an ISO date") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def hex_tokens(text: str) -> set[str]:
    """Standalone lowercase hex tokens of 7..40 chars — candidate SHA mentions."""
    return set(HEX_TOKEN_RE.findall(text or ""))


def sha_named_in(text: str, sha: str) -> str | None:
    """Return the token by which ``sha`` is named in ``text``, or None.

    A 7+ char prefix counts as naming the SHA; anything shorter is too weak to
    be evidence.
    """
    for token in hex_tokens(text):
        if sha.startswith(token):
            return token
    return None


def issue_refs(text: str) -> list[str]:
    """Ticket ids in commit-message order, de-duplicated."""
    seen: list[str] = []
    for ref in ISSUE_REF_RE.findall(text or ""):
        if ref not in seen:
            seen.append(ref)
    return seen


def is_qa_approval(body: str) -> bool:
    # A later declaration can withdraw an earlier verdict, but the negative
    # must govern this candidate/verdict.  Matching those words anywhere in a
    # long QA report wrongly vetoes approvals that discuss revoked signatures,
    # withdrawn findings, or a superseded candidate.
    for line in (body or "").splitlines():
        prose = re.sub(r"`[^`]*`", "", line)
        if re.search(
            r"\b(?:this|the current)\s+(?:candidate|approval|verdict)\b[^.;]{0,80}"
            r"\b(?:NOT APPROVED|WITHDRAWN|RETRACTED|REVOKED)\b"
            r"|\b(?:this\s+)?approval\b[^.;]{0,40}\b(?:WITHDRAWN|RETRACTED|REVOKED)\b"
            r"|\b(?-i:APPROVED)\b[^.;]{0,40}\b(?:WITHDRAWN|RETRACTED|REVOKED)\b"
            r"|^\s*[*_`#-]*\s*(?:NOT APPROVED|WITHDRAWN|RETRACTED|REVOKED)"
            r"\s*[.!:;*_`-]*\s*$",
            prose,
            re.IGNORECASE,
        ):
            return False
    prior_negative_verdict = False
    for line in (body or "").splitlines():
        # A QA handoff may describe the candidate transition using either real
        # SHAs or the gate's prescribed ``<old> -> <new>`` placeholders. Remove
        # that bookkeeping before stripping inline code so backticked tokens do
        # not turn an otherwise valid pair into the misleading text `` -> ``.
        transition = r"(?:[0-9a-f]{7,40}|<[A-Za-z0-9_.-]+>)"
        supersession_note_re = re.compile(
            rf"\bSUPERSEDED\s*:?[ ]*`?{transition}`?\s*->\s*"
            rf"`?{transition}`?\s*[.]?",
            re.IGNORECASE,
        )
        line_without_bookkeeping = supersession_note_re.sub("", line)
        # Inline code is evidence or discussion, never the comment's verdict.
        # In particular, a later ``is_qa_approval("**APPROVED** ...")`` citation
        # must not override an earlier CHANGES REQUESTED verdict.
        verdict_line = re.sub(r"`[^`]*`", "", line_without_bookkeeping)
        if not QA_APPROVED_RE.search(verdict_line):
            if QA_NEGATIVE_RE.search(verdict_line):
                prior_negative_verdict = True
            continue
        if prior_negative_verdict:
            return False
        # A canonical QA re-test may record the candidate transition after its
        # verdict on the same line.  That ``SUPERSEDED: old -> new`` note names
        # the retired candidate; it does not supersede the approval immediately
        # preceding it.  Keep the exemption deliberately SHA-shaped so prose
        # such as "SUPERSEDED - prior APPROVED retracted" remains fail-closed.
        # Remove only the SHA-shaped bookkeeping clause. Real QA comments often
        # continue with an ordinary sentence after it; that trailing prose must
        # still be inspected for an independent negative verdict.
        negative_scope = verdict_line
        # QA also uses a backticked ``SUPERSEDED`` heading, with the old/new
        # pair on the next line. The code-span removal above leaves the genuine
        # APPROVED verdict and removes that bookkeeping label.
        negative_scope = re.sub(r"`SUPERSEDED`", "", negative_scope,
                                flags=re.IGNORECASE)
        # Markdown emphasis is presentation, not clause structure. Remove its
        # delimiters, then exempt only the narrow affirmative phrases that say
        # the approval has no blocker. Every remaining negative verdict token
        # on the line remains fail-closed, regardless of its position.
        negative_scope = negative_scope.replace("**", "").replace("__", "")
        negative_scope = re.sub(
            r"\bnot blocked by anything\b|\bnothing(?: here)? is blocked\b|\bnothing rejected\b",
            "",
            negative_scope,
            flags=re.IGNORECASE,
        )
        negative_scope = re.sub(
            r"\bresolves?\b[^.;\n]{0,80}\bCHANGES[- ]REQUESTED\b"
            r"[^.;\n]{0,80}\b(?:prior|earlier)\s+review\b",
            "",
            negative_scope,
            flags=re.IGNORECASE,
        )
        cleaned_stripped = re.sub(
            r"^\s*(?:(?:[-*+]|\d+[.)])\s+|#+\s*)*[*_`]*", "", negative_scope
        )
        cleaned_label = re.sub(
            r"^[A-Za-z][A-Za-z0-9 ()_-]{0,40}[:\u2013\u2014-]\s*[*_`]*",
            "", cleaned_stripped, count=1,
        )
        negative_at_verdict = bool(
            QA_NEGATIVE_RE.match(cleaned_stripped) or QA_NEGATIVE_RE.match(cleaned_label)
        )
        negative = negative_at_verdict or bool(QA_NEGATIVE_RE.search(negative_scope))
        if not negative:
            return True
    return False


def _unquoted_prose(text: str) -> str:
    """Remove quoted prose that cannot declare the current ticket's state."""
    kept: list[str] = []
    for line in (text or "").splitlines():
        if re.match(r"\s*>", line):
            continue
        def remove_or_unquote(match: re.Match[str]) -> str:
            prefix = line[:match.start()]
            if re.search(r"\bclassification\s*[:=]\s*$", prefix, re.IGNORECASE):
                return match.group(2)
            return ""

        line = re.sub(r'(["\u201c])(.*?)(["\u201d])', remove_or_unquote, line)
        kept.append(line)
    return "\n".join(kept)


def cosign_strength(body: str, named_token: str | None = None) -> int:
    """0 = not a co-sign, 1 = refers to one, 2 = grants one for ``named_token``.

    Free-text co-signs cannot be classified perfectly, so the reconciler errs
    towards 1: a landing reported as unreconciled cites the comment id it
    downgraded, and a reader can overrule it. Erring the other way would let a
    closure report stand in for the gate.
    """
    body = body or ""
    if _cosign_negative_declaration(body):
        return 0
    if COSIGN_GRANT_RE.search(body):
        return 2
    if named_token and re.search(COSIGN_REPORT_RE.pattern % re.escape(named_token),
                                 body, re.IGNORECASE):
        return 1
    if named_token and re.search(COSIGN_ASSERTION_RE.pattern % re.escape(named_token),
                                 body, re.IGNORECASE):
        return 2
    if COSIGN_MENTION_RE.search(body):
        return 1
    return 0


def ticket_classification(texts: list[str], identifier: str | None = None) -> str:
    """Read a ticket's own rule-change / rule-execution classification.

    ``texts`` are the ticket's description then its comments, oldest first. The
    last explicit declaration wins, because a reclassification is recorded as a
    later comment ("classification: rule execution -> **rule change**").

    Only declarations whose subject is this ticket count. A ticket that quotes
    the classification table without classifying itself stays ``UNCLASSIFIED``,
    and an unclassified ticket never produces a co-sign finding — v2.0 puts the
    requirement on rule changes, so a co-sign is only missing where the ticket
    says one was owed.
    """
    subject = _CLASS_SUBJECT
    if identifier:
        subject = f"(?:{_CLASS_SUBJECT}|{re.escape(identifier)})"
    declaration_re = re.compile(
        CLASS_DECLARATION_TEMPLATE.format(subject=subject), re.IGNORECASE
    )

    def conditional_candidate(text: str, value_start: int) -> bool:
        """Whether the candidate is the outcome of a hypothetical condition."""
        clause = re.split(r"[.;]", text[:value_start])[-1]
        return bool(re.search(
            r"\b(?:(?:would|will)\s+re-?classif(?:y|ies|ied)|re-?classifies)\b"
            r"[^.;]{0,48}\bas\s+"
            r"(?:an?\s+)?[*_`]{0,2}$",
            clause,
            re.I,
        ))

    verdict = UNCLASSIFIED
    for text in texts:
        text = _unquoted_prose(text or "")
        latest: tuple[int, str] | None = None
        for field in CLASS_FIELD_RE.finditer(text):
            span = field.group(0)
            values = []
            for value in CLASS_VALUE_RE.finditer(span):
                prefix = span[:value.start()]
                immediate_negation = re.search(
                    r"(?:\b(?:not|no|never)\s+(?:an?\s+)?|n['\u2019]t\s+(?:an?\s+)?|"
                    r"\b(?:rather\s+than|instead\s+of)\s+(?:an?\s+)?)$",
                    prefix[-48:],
                    re.I,
                )
                clause = re.split(r"[.;]", prefix)[-1]
                # "No rubric ... rule changed" is a denial; "No - ... so
                # rule change" is a correction. Punctuation after No carries
                # that distinction without vetoing the corrected value.
                broad_no_clause = re.match(r"\s*no\b(?!\s*[-\u2013\u2014,:])", clause, re.I)
                if not (immediate_negation or broad_no_clause
                        or conditional_candidate(span, value.start())):
                    values.append(value.group(1))
            if values:  # a corrected field states both; the last one is current
                latest = (field.start(), values[-1].lower())
        for match in declaration_re.finditer(text):
            value = next(g for g in match.groups() if g)
            value_start = next(match.start(i) for i, g in enumerate(match.groups(), 1) if g)
            candidate_start = text.rfind("rule", match.start(), value_start + 1)
            if (not conditional_candidate(text, candidate_start)
                    and (latest is None or match.start() > latest[0])):
                latest = (match.start(), value.lower())
        if latest:
            verdict = RULE_CHANGE if latest[1] == "change" else RULE_EXECUTION
    return verdict


def merged_sha_field_present(body: str) -> bool:
    """Whether the closure names the field at all, cited value or not."""
    return bool(MERGED_SHA_FIELD_RE.search(body or ""))


def merged_sha_citations(body: str) -> list[str]:
    """SHAs cited as the *value* of ``merged_sha_on_canon_master_at_close``.

    Only the token immediately following the field name and its separator
    counts. Anything later in the line or the comment is prose about the field,
    not the field.
    """
    out: list[str] = []
    for match in MERGED_SHA_VALUE_RE.finditer(body or ""):
        token = match.group(1)
        if token == token.lower():  # git writes lowercase; stay with hex_tokens()
            out.append(token)
    return out


def classify(
    landing: Landing,
    issue: str | None,
    cosign_comment_id: str | None,
    qa_comment_id: str | None,
    matched_sha: str | None,
    qa_other_shas: list[str],
    predecessor_shas: list[str],
    exempt_reason: str | None,
    cosign_mention_id: str | None = None,
    ticket_class: str = UNCLASSIFIED,
    merged: MergedShaEvidence | None = None,
    branch: str = DEFAULT_BRANCH,
) -> Row:
    """Decide the verdict for one landing. Pure — all lookups already done.

    Three legs are evaluated, and a landing is ``reconciled`` only if every leg
    that applies to it passes:

    * the QA exact-SHA leg, which ``qa-gate-lifecycle`` applies to every shared
      promotion, and landing on canon ``master`` is one;
    * the co-sign leg, which v2.0 applies **only** to rule changes;
    * the ``merged_sha_on_canon_master_at_close`` leg, which v2.0 applies to
      every ticket that ships code regardless of classification.

    Each failing leg is recorded in ``findings`` so the legs can be counted
    apart. Folding them into one number is what turned a single-leg gap into a
    company-wide-indiscipline reading (BEAAA-24491).
    """
    date = landing.head.committed_at
    if exempt_reason:
        return Row(landing.sha, date, issue, cosign_comment_id, qa_comment_id, EXEMPT, exempt_reason)

    if not issue:
        # No ticket, so no closure record: every leg fails for one upstream
        # reason. Tagged as well as counted, so the bucket stays visible as the
        # subset it is rather than reading as three independent failures.
        return Row(
            landing.sha, date, None, None, None, UNRECONCILED,
            "no Paperclip issue resolves this landing: no ticket id in the commit "
            "message and no issue comment names the SHA",
            ticket_class=UNCLASSIFIED,
            findings=[FINDING_UNRESOLVED, FINDING_QA, FINDING_MERGED_SHA],
        )

    merged = merged or MergedShaEvidence()
    findings: list[str] = []
    missing: list[str] = []

    # --- co-sign leg: scoped to rule changes -----------------------------
    if ticket_class == RULE_CHANGE and not cosign_comment_id:
        findings.append(FINDING_COSIGN)
        if cosign_mention_id:
            missing.append(
                f"ticket classified rule change; CTO comment {cosign_mention_id[:8]} refers "
                "to a co-sign but grants none for an exact SHA of this landing"
            )
        else:
            missing.append(
                "ticket classified rule change with no CTO co-sign naming an exact SHA "
                "of this landing"
            )

    # --- QA exact-SHA leg: universal ------------------------------------
    if not qa_comment_id:
        findings.append(FINDING_QA)
        if predecessor_shas:
            missing.append(
                "QA APPROVED names predecessor "
                + ", ".join(s[:12] for s in predecessor_shas)
                + ", not this landing"
            )
        elif qa_other_shas:
            missing.append(
                "QA APPROVED names unrelated SHA "
                + ", ".join(sorted({s[:12] for s in qa_other_shas}))
            )
        else:
            missing.append("no QA APPROVED naming an exact SHA of this landing")

    # --- merged-SHA leg: universal, matches commit-shape ------------------
    if not merged.field_present:
        findings.append(FINDING_MERGED_SHA)
        missing.append("closure quotes no merged_sha_on_canon_master_at_close")
    elif not merged.token:
        findings.append(FINDING_MERGED_SHA)
        missing.append(
            "merged_sha_on_canon_master_at_close is named but cites no SHA"
        )
    elif not merged.resolved:
        findings.append(FINDING_MERGED_SHA)
        missing.append(
            f"merged_sha_on_canon_master_at_close cites {merged.token}, "
            f"which does not resolve on canon {branch}"
        )
    elif not merged.reachable:
        findings.append(FINDING_MERGED_SHA)
        missing.append(
            f"merged_sha_on_canon_master_at_close cites {merged.resolved[:12]}, "
            f"not reachable from canon {branch} HEAD"
        )

    if not findings:
        held = []
        if ticket_class == RULE_CHANGE:
            held.append("CTO co-sign granted")
        held.append(f"QA APPROVED names {matched_sha}")
        held.append(f"merged sha {merged.resolved[:12]} reachable from canon {branch} HEAD")
        return Row(
            landing.sha, date, issue, cosign_comment_id, qa_comment_id,
            RECONCILED, "; ".join(held), matched_sha,
            ticket_class=ticket_class, merged_sha=merged.resolved,
            merged_sha_comment_id=merged.comment_id,
        )

    return Row(
        landing.sha, date, issue, cosign_comment_id, qa_comment_id,
        UNRECONCILED, "; ".join(missing), matched_sha,
        ticket_class=ticket_class, merged_sha=merged.resolved,
        merged_sha_comment_id=merged.comment_id, findings=findings,
    )


#: rule-execution tickets are not asked for a co-sign, so their column reads
#: "n/a" rather than "-": absent and not-required are different states, and
#: printing them the same way is how a scoped column reads as a gap.
def _cosign_cell(row: Row) -> str:
    if row.cosign_comment_id:
        return row.cosign_comment_id[:8]
    if row.classification == EXEMPT:
        return "-"
    return "-" if row.ticket_class == RULE_CHANGE else "n/a"


def _ticket_class_cell(row: Row) -> str:
    return {RULE_CHANGE: "change", RULE_EXECUTION: "exec", UNCLASSIFIED: "?"}.get(
        row.ticket_class, "-"
    )


def render_table(rows: list[Row]) -> str:
    headers = ["SHA", "DATE", "ISSUE", "TICKET", "CO-SIGN", "QA", "MERGED-SHA", "CLASS", "REASON"]
    data = [
        [
            r.sha[:12],
            r.date[:19],
            r.issue or "-",
            _ticket_class_cell(r),
            _cosign_cell(r),
            (r.qa_comment_id or "-")[:8],
            (r.merged_sha or "-")[:12],
            r.classification,
            r.reason,
        ]
        for r in rows
    ]
    widths = [max(len(h), *(len(row[i]) for row in data)) if data else len(h)
              for i, h in enumerate(headers)]
    # the reason column is free-text; do not pad it out to the terminal edge
    widths[-1] = len(headers[-1])
    out = [" ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()]
    out.append(" ".join("-" * w for w in widths).rstrip())
    for row in data:
        out.append(" ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return "\n".join(out)


def render_markdown(rows: list[Row]) -> str:
    out = ["| SHA | Date | Issue | Ticket class | Co-sign comment | QA comment | Merged SHA | Classification | Reason |",
           "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        cosign = _cosign_cell(r)
        out.append(
            "| `{sha}` | {date} | {issue} | {tcls} | {cosign} | {qa} | {merged} | {cls} | {reason} |".format(
                sha=r.sha[:12],
                date=r.date[:10],
                issue=r.issue or "—",
                tcls=r.ticket_class or "—",
                cosign=f"`{cosign}`" if cosign not in ("-", "n/a") else ("n/a" if cosign == "n/a" else "—"),
                qa=f"`{r.qa_comment_id[:8]}`" if r.qa_comment_id else "—",
                merged=f"`{r.merged_sha[:12]}`" if r.merged_sha else "—",
                cls=r.classification,
                reason=r.reason.replace("|", "\\|"),
            )
        )
    return "\n".join(out)


def summarise(rows: list[Row]) -> dict[str, int]:
    counts = {RECONCILED: 0, UNRECONCILED: 0, EXEMPT: 0}
    for r in rows:
        counts[r.classification] = counts.get(r.classification, 0) + 1
    return counts


def summarise_findings(rows: list[Row]) -> dict[str, int]:
    """Count each leg separately. A landing can fail more than one.

    ``unresolved`` is a subset of the legs it forces, not a fourth class, so
    these counts do not sum to the ``unreconciled`` total and are not meant to.
    """
    counts = {leg: 0 for leg in (*FINDING_LEGS, FINDING_UNRESOLVED)}
    for row in rows:
        for leg in row.findings:
            counts[leg] = counts.get(leg, 0) + 1
    return counts


def summarise_ticket_classes(rows: list[Row]) -> dict[str, int]:
    """How the landings' resolving tickets classify themselves.

    Reported because the co-sign column is only meaningful over the rule-change
    slice, and a reader needs to see how big that slice is before reading a
    co-sign count. Not a finding: an unclassified ticket is not charged one.
    """
    counts = {RULE_CHANGE: 0, RULE_EXECUTION: 0, UNCLASSIFIED: 0}
    for row in rows:
        if row.classification == EXEMPT or row.ticket_class is None:
            continue
        counts[row.ticket_class] = counts.get(row.ticket_class, 0) + 1
    return counts


# --------------------------------------------------------------------------
# git — read-only against the remote (clone/fetch only)
# --------------------------------------------------------------------------

_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"
_LOG_FORMAT = _FIELD_SEP.join(["%H", "%cI", "%T", "%P", "%s", "%b"]) + _RECORD_SEP


class CanonRepo:
    """A blobless bare mirror of the canon branch. Nothing is ever written back."""

    def __init__(self, cache_dir: str, repo: str, branch: str):
        self.dir = cache_dir
        self.repo = repo
        self.branch = branch
        self.url = f"https://github.com/{repo}.git"
        self._resolve_cache: dict[str, str | None] = {}
        self._ancestor_cache: dict[tuple[str, str], bool] = {}
        self._lock = threading.Lock()

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "--git-dir", self.dir, *args],
            check=True, capture_output=True, text=True,
        ).stdout

    def sync(self) -> None:
        if not os.path.isdir(os.path.join(self.dir, "objects")):
            os.makedirs(os.path.dirname(self.dir) or ".", exist_ok=True)
            subprocess.run(
                ["git", "clone", "--bare", "--filter=blob:none", "--single-branch",
                 "--branch", self.branch, self.url, self.dir],
                check=True, capture_output=True, text=True,
            )
            return
        self._git("fetch", "--filter=blob:none", "--prune", "origin",
                  f"+refs/heads/{self.branch}:refs/heads/{self.branch}")

    def _parse_log(self, out: str) -> list[Commit]:
        commits = []
        for record in out.split(_RECORD_SEP):
            record = record.strip("\n")
            if not record:
                continue
            sha, cdate, tree, parents, subject, body = record.split(_FIELD_SEP)
            commits.append(Commit(
                sha=sha, committed_at=cdate, tree=tree,
                parents=tuple(p for p in parents.split() if p),
                subject=subject, body=body,
            ))
        return commits

    def first_parent(self, since: dt.datetime, until: dt.datetime | None) -> list[Commit]:
        args = ["log", "--first-parent", f"--since={since.isoformat()}",
                f"--pretty=format:{_LOG_FORMAT}"]
        if until:
            args.insert(3, f"--until={until.isoformat()}")
        args.append(f"refs/heads/{self.branch}")
        return self._parse_log(self._git(*args))

    def side_branch(self, merge: Commit) -> list[Commit]:
        """Commits this merge brought in: reachable from a non-first parent, not the first."""
        if len(merge.parents) < 2:
            return []
        args = ["log", f"--pretty=format:{_LOG_FORMAT}", *merge.parents[1:],
                "--not", merge.parents[0]]
        try:
            return self._parse_log(self._git(*args))
        except subprocess.CalledProcessError:
            # A side branch can be unreachable if it was never pushed to this
            # mirror's branch history. Report what we have rather than crash.
            return []

    def tree_of(self, sha: str) -> str | None:
        try:
            return self._git("rev-parse", f"{sha}^{{tree}}").strip()
        except subprocess.CalledProcessError:
            return None

    def resolve(self, rev: str) -> str | None:
        with self._lock:
            if rev in self._resolve_cache:
                return self._resolve_cache[rev]
        try:
            value = self._git("rev-parse", "--verify", f"{rev}^{{commit}}").strip()
        except subprocess.CalledProcessError:
            value = None
        with self._lock:
            self._resolve_cache[rev] = value
        return value

    def is_ancestor(self, maybe_ancestor: str, descendant: str) -> bool:
        key = (maybe_ancestor, descendant)
        with self._lock:
            if key in self._ancestor_cache:
                return self._ancestor_cache[key]
        proc = subprocess.run(
            ["git", "--git-dir", self.dir, "merge-base", "--is-ancestor",
             maybe_ancestor, descendant],
            capture_output=True, text=True,
        )
        with self._lock:
            self._ancestor_cache[key] = proc.returncode == 0
        return self._ancestor_cache[key]

    def head(self) -> str | None:
        """The canon branch tip in this mirror.

        The mirror's ``refs/heads/<branch>`` is force-updated from
        ``origin`` on every ``sync()``, so this is the canonical remote head —
        not a local branch that may sit behind it. Resolving ``master`` against
        a stale local checkout is the defect BEAAA-24536 F1 raises against the
        contract's own wording, and reachability answered against a stale head
        flags correctly merged work Founder-tier.
        """
        return self.resolve(f"refs/heads/{self.branch}")

    def landings(self, since: dt.datetime, until: dt.datetime | None) -> list[Landing]:
        out = []
        for head in self.first_parent(since, until):
            parent_tree = self.tree_of(head.parents[0]) if head.parents else None
            out.append(Landing(head=head, side=self.side_branch(head), parent_tree=parent_tree))
        return out


# --------------------------------------------------------------------------
# Paperclip — read-only (GET only)
# --------------------------------------------------------------------------


class PaperclipClient:
    #: A 30-day window is a few hundred searches over comment full-text. One slow
    #: response should not discard twenty minutes of work.
    TIMEOUT_SEC = 120
    ATTEMPTS = 4

    def __init__(self, base_url: str, api_key: str, company_id: str):
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.company = company_id
        self._issue_cache: dict[str, dict | None] = {}
        self._comment_cache: dict[str, list[dict]] = {}
        self._search_cache: dict[str, list[dict]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self.calls = 0

    def _lock_for(self, key: str) -> threading.Lock:
        """One lock per cache key: two landings citing the same ticket wait for
        one fetch instead of racing to issue the same search."""
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def _get(self, path: str, **params) -> object:
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.key}"})
        self.calls += 1
        for attempt in range(1, self.ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.TIMEOUT_SEC) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
                if exc.code not in (429, 500, 502, 503, 504) or attempt == self.ATTEMPTS:
                    raise
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == self.ATTEMPTS:
                    raise
            time.sleep(2 ** attempt)
        return None

    @staticmethod
    def _list(payload: object, *keys: str) -> list[dict]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in keys:
                if isinstance(payload.get(key), list):
                    return payload[key]
        return []

    def agents_by_role(self) -> dict[str, set[str]]:
        payload = self._get(f"/api/companies/{self.company}/agents")
        roles: dict[str, set[str]] = {}
        for agent in self._list(payload, "agents", "items"):
            roles.setdefault((agent.get("role") or "").lower(), set()).add(agent["id"])
        return roles

    def search_issues(self, query: str) -> list[dict]:
        with self._lock_for(f"search:{query}"):
            if query not in self._search_cache:
                payload = self._get(f"/api/companies/{self.company}/issues", q=query)
                self._search_cache[query] = self._list(payload, "issues", "items")
        return self._search_cache[query]

    def issue_by_identifier(self, identifier: str) -> dict | None:
        with self._lock_for(f"ident:{identifier}"):
            if identifier in self._issue_cache:
                return self._issue_cache[identifier]
        match = next(
            (i for i in self.search_issues(identifier) if i.get("identifier") == identifier),
            None,
        )
        self._issue_cache[identifier] = match
        return match

    def issue(self, issue_id: str) -> dict | None:
        key = f"id:{issue_id}"
        with self._lock_for(key):
            if key not in self._issue_cache:
                self._issue_cache[key] = self._get(f"/api/issues/{issue_id}")
        return self._issue_cache[key]

    def comments(self, issue_id: str) -> list[dict]:
        with self._lock_for(f"comments:{issue_id}"):
            if issue_id not in self._comment_cache:
                payload = self._get(f"/api/issues/{issue_id}/comments")
                self._comment_cache[issue_id] = self._list(payload, "comments", "items")
        return self._comment_cache[issue_id]

    def neighbourhood(self, issue: dict) -> list[dict]:
        """The issues that can legitimately hold this landing's evidence.

        A QA ``APPROVED`` normally lives on a canonical QA-gate child, not on
        the implementation issue the commit message names, so the search has to
        widen past the referenced ticket: its ancestors, the tickets it
        references, and the tickets that reference it back.
        """
        out = {issue["id"]: issue}
        for ancestor in self._list(issue.get("ancestors"), "items"):
            out.setdefault(ancestor["id"], ancestor)
        for link in self._list((issue.get("relatedWork") or {}).get("outbound"), "items"):
            linked = link.get("issue") or {}
            if linked.get("id"):
                out.setdefault(linked["id"], linked)
        for identifier in (issue.get("referencedIssueIdentifiers") or []):
            found = self.issue_by_identifier(identifier)
            if found and found.get("id"):
                out.setdefault(found["id"], found)
        return list(out.values())


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------


@dataclasses.dataclass
class Evidence:
    cosign_comment_id: str | None = None
    cosign_mention_id: str | None = None
    qa_comment_id: str | None = None
    matched_sha: str | None = None
    qa_other_shas: list[str] = dataclasses.field(default_factory=list)


def find_evidence(
    comments: list[dict],
    evidence_shas: list[str],
    cosign_agents: set[str],
    qa_agents: set[str],
) -> Evidence:
    """Scan comments for a co-sign grant and a QA APPROVED naming this landing.

    Only a *grant* counts as the co-sign. A CTO comment that refers to a co-sign
    is recorded separately as a mention, because treating it as the control
    would let a closure report stand in for the gate and silently reconcile a
    landing that was never co-signed.

    ``qa_other_shas`` holds SHAs named by a QA approval that is not about this
    landing — the raw material for a "names a predecessor" finding.
    """
    found = Evidence()
    qa_other: set[str] = set()
    # Oldest first: the grant precedes the merge report that cites it.
    for comment in sorted(comments, key=lambda c: c.get("createdAt") or ""):
        body = comment.get("body") or ""
        author = comment.get("authorAgentId")
        named_token = None
        named = None
        for candidate in evidence_shas:
            token = sha_named_in(body, candidate)
            if token:
                named, named_token = candidate, token
                break
        if author in cosign_agents and named:
            strength = cosign_strength(body, named_token)
            if strength == 2 and not found.cosign_comment_id:
                found.cosign_comment_id = comment["id"]
                found.matched_sha = found.matched_sha or named
            elif strength == 1 and not found.cosign_mention_id:
                found.cosign_mention_id = comment["id"]
        if author in qa_agents and is_qa_approval(body):
            if named:
                if not found.qa_comment_id:
                    found.qa_comment_id = comment["id"]
                    found.matched_sha = found.matched_sha or named
            else:
                qa_other |= hex_tokens(body)
    found.qa_other_shas = sorted(qa_other)
    return found


def find_merged_sha(
    comments: list[dict],
    landing: Landing,
    repo: "CanonRepo",
    head_sha: str | None,
) -> MergedShaEvidence:
    """Find the closure's ``merged_sha_on_canon_master_at_close`` and verify it.

    The verification is ``git merge-base --is-ancestor <cited> <canon HEAD>``,
    the same proof routine ``161a7f8a`` already runs nightly over
    ``security``-labelled closures. Reusing that shape rather than inventing a
    second one is deliberate: two reachability checks that disagree would be
    worse than none.

    A citation naming a SHA of *this* landing is preferred, but any cited SHA
    that resolves and is reachable satisfies the field — a ticket that shipped
    several landings closes once, quoting one of them, and reporting the others
    as unproven would be a finding the contract does not make.
    """
    best = MergedShaEvidence()
    for comment in sorted(comments, key=lambda c: c.get("createdAt") or ""):
        body = comment.get("body") or ""
        if merged_sha_field_present(body) and not best.field_present:
            best = dataclasses.replace(best, field_present=True,
                                       comment_id=best.comment_id or comment["id"])
        for token in merged_sha_citations(body):
            resolved = repo.resolve(token)
            names_landing = bool(resolved and resolved in landing.member_shas)
            candidate = MergedShaEvidence(
                comment_id=comment["id"],
                token=token,
                resolved=resolved,
                reachable=bool(resolved and head_sha and repo.is_ancestor(resolved, head_sha)),
                names_landing=names_landing,
                field_present=True,
            )
            # rank: names this landing and reachable > reachable > resolves >
            # cited at all. The first citation that names this landing wins.
            if _merged_rank(candidate) > _merged_rank(best):
                best = candidate
            if candidate.names_landing and candidate.reachable:
                return candidate
    return best


def _merged_rank(evidence: MergedShaEvidence) -> int:
    """Rank citations so the row reports the strongest one the closure offers.

    Ordered so a more specific failure always beats a vaguer one: a citation
    that does not resolve is a better thing to print than "cites no SHA".
    """
    if not evidence.field_present:
        return 0
    if not evidence.token:
        return 1
    if not evidence.resolved:
        return 2
    if not evidence.reachable:
        return 3
    return 5 if evidence.names_landing else 4


def resolve_landing(
    landing: Landing,
    client: PaperclipClient,
    repo: CanonRepo,
    cosign_agents: set[str],
    qa_agents: set[str],
    exempt_before: dt.datetime | None,
    head_sha: str | None = None,
) -> Row:
    exempt_reason = None
    committed = dt.datetime.fromisoformat(landing.head.committed_at)
    if exempt_before and committed < exempt_before:
        exempt_reason = (
            "landed before the co-sign control took effect "
            f"({exempt_before.date().isoformat()})"
        )
    elif landing.introduces_no_tree_change:
        exempt_reason = "landing changes no tree on canon (tree identical to first parent)"
    if exempt_reason:
        return classify(landing, None, None, None, None, [], [], exempt_reason)

    owning_issue: str | None = None
    owning_id: str | None = None
    neighbourhood: dict[str, dict] = {}

    for identifier in issue_refs("\n".join(landing.messages)):
        issue = client.issue_by_identifier(identifier)
        if not issue:
            continue
        if owning_issue is None:
            owning_issue, owning_id = identifier, issue["id"]
        full = client.issue(issue["id"]) or issue
        for near in client.neighbourhood(full):
            neighbourhood.setdefault(near["id"], near)

    if not neighbourhood:
        # No resolvable ticket id in the message. Ask Paperclip which issue talks
        # about this SHA, then verify the mention rather than trusting the rank.
        for sha in landing.evidence_shas:
            for hit in client.search_issues(sha[:12]):
                if hit["id"] in neighbourhood:
                    continue
                if any(sha_named_in(c.get("body") or "", sha) for c in client.comments(hit["id"])):
                    full = client.issue(hit["id"]) or hit
                    for near in client.neighbourhood(full):
                        neighbourhood.setdefault(near["id"], near)
                    if owning_issue is None:
                        owning_issue, owning_id = hit.get("identifier"), hit["id"]

    if not neighbourhood:
        return classify(landing, None, None, None, None, [], [], None)

    # The co-sign and the QA approval routinely live on different issues — the
    # implementation ticket carries the co-sign, the canonical QA-gate child
    # carries the APPROVED. Search the whole neighbourhood for each.
    all_comments = [c for issue in neighbourhood.values() for c in client.comments(issue["id"])]
    found = find_evidence(all_comments, landing.evidence_shas, cosign_agents, qa_agents)
    predecessors = sorted({
        resolved for token in found.qa_other_shas
        if (resolved := repo.resolve(token)) and repo.is_ancestor(resolved, landing.sha)
    })

    # The classification is the *owning* ticket's own call, not the
    # neighbourhood's: a QA-gate child does not classify the implementation it
    # gates, and an ancestor's classification is not inherited downward.
    ticket_class = UNCLASSIFIED
    if owning_id:
        owner = client.issue(owning_id) or neighbourhood.get(owning_id) or {}
        texts = [owner.get("description") or ""]
        texts += [
            c.get("body") or ""
            for c in sorted(client.comments(owning_id), key=lambda c: c.get("createdAt") or "")
        ]
        ticket_class = ticket_classification(texts, owning_issue)

    merged = find_merged_sha(all_comments, landing, repo, head_sha)

    return classify(
        landing, owning_issue, found.cosign_comment_id, found.qa_comment_id,
        found.matched_sha, found.qa_other_shas if not predecessors else [],
        predecessors, None, found.cosign_mention_id,
        ticket_class=ticket_class, merged=merged, branch=repo.branch,
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="landing_reconciler",
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--since", default="30d",
                        help="window start: 30d, 2w, 12h, or an ISO date (default: 30d)")
    parser.add_argument("--until", default=None, help="window end as an ISO date (default: now)")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--cache-dir", default=os.path.expanduser("~/.cache/canon-landing-reconciler/mirror.git"),
                        help="bare mirror of the canon branch; created on first run")
    parser.add_argument("--no-fetch", action="store_true",
                        help="use the mirror as-is instead of fetching (offline / tests)")
    parser.add_argument("--exempt-before", default=None,
                        help="ISO date before which landings are exempt, with that reason stated")
    parser.add_argument("--cosign-role", default="cto", help="Paperclip role that grants a merge co-sign")
    parser.add_argument("--qa-role", default="qa", help="Paperclip role that records a QA APPROVED")
    parser.add_argument("--concurrency", type=int, default=8,
                        help="landings resolved in parallel; the work is Paperclip I/O (default: 8)")
    parser.add_argument("--format", choices=["table", "markdown", "json"], default="table")
    parser.add_argument("--fail-on-unreconciled", dest="fail", action="store_true", default=True)
    parser.add_argument("--no-fail-on-unreconciled", dest="fail", action="store_false")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now = dt.datetime.now(dt.timezone.utc)
    since = parse_window(args.since, now)
    until = parse_window(args.until, now) if args.until else None
    exempt_before = parse_window(args.exempt_before, now) if args.exempt_before else None

    api_url = os.environ.get("PAPERCLIP_API_URL")
    api_key = os.environ.get("PAPERCLIP_API_KEY")
    company = os.environ.get("PAPERCLIP_COMPANY_ID")
    if not (api_url and api_key and company):
        print("PAPERCLIP_API_URL, PAPERCLIP_API_KEY and PAPERCLIP_COMPANY_ID must be set",
              file=sys.stderr)
        return 2

    repo = CanonRepo(args.cache_dir, args.repo, args.branch)
    if not args.no_fetch:
        try:
            repo.sync()
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            print(f"cannot sync canon {args.repo} branch {args.branch}: {detail}",
                  file=sys.stderr)
            return 2
    head_sha = repo.head()
    if head_sha is None:
        print(f"cannot resolve canon {args.branch} HEAD in {args.cache_dir}; "
              "the merged-SHA reachability leg would report every landing unreachable",
              file=sys.stderr)
        return 2
    landings = repo.landings(since, until)

    client = PaperclipClient(api_url, api_key, company)
    roles = client.agents_by_role()
    cosign_agents = roles.get(args.cosign_role.lower(), set())
    qa_agents = roles.get(args.qa_role.lower(), set())
    if not cosign_agents or not qa_agents:
        print(f"no agent holds role {args.cosign_role!r} or {args.qa_role!r}; "
              "every landing would report unreconciled for the wrong reason",
              file=sys.stderr)
        return 2

    show_progress = sys.stderr.isatty() or bool(os.environ.get("RECONCILER_PROGRESS"))
    rows: list[Row | None] = [None] * len(landings)
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {
            pool.submit(resolve_landing, landing, client, repo,
                        cosign_agents, qa_agents, exempt_before, head_sha): index
            for index, landing in enumerate(landings)
        }
        for future in concurrent.futures.as_completed(futures):
            rows[futures[future]] = future.result()
            done += 1
            if show_progress:
                print(f"\r  {done}/{len(landings)} landings", end="", file=sys.stderr, flush=True)
    if show_progress:
        print("", file=sys.stderr)
    rows = [row for row in rows if row is not None]

    counts = summarise(rows)
    legs = summarise_findings(rows)
    classes = summarise_ticket_classes(rows)
    window = f"{since.isoformat()} .. {(until or now).isoformat()}"
    if args.format == "json":
        print(json.dumps({
            "repo": args.repo, "branch": args.branch, "window": window,
            "canon_head": head_sha,
            "counts": counts, "findings": legs, "ticket_classes": classes,
            "landings": [r.as_dict() for r in rows],
        }, indent=2))
    else:
        renderer = render_markdown if args.format == "markdown" else render_table
        print(f"{args.repo} refs/heads/{args.branch} — {window}")
        print(f"canon HEAD {head_sha[:12]}")
        print(f"{len(rows)} landings: "
              f"{counts[RECONCILED]} reconciled, {counts[UNRECONCILED]} unreconciled, "
              f"{counts[EXEMPT]} exempt")
        print("findings by leg — "
              f"{legs[FINDING_QA]} missing the QA exact-SHA approval, "
              f"{legs[FINDING_COSIGN]} missing a CTO co-sign the ticket's own classification "
              "required, "
              f"{legs[FINDING_MERGED_SHA]} missing merged_sha_on_canon_master_at_close")
        print(f"  of which {legs[FINDING_UNRESOLVED]} resolve to no Paperclip issue at all "
              "(a subset, not a fourth leg)")
        print("resolving tickets — "
              f"{classes[RULE_CHANGE]} rule change, {classes[RULE_EXECUTION]} rule execution, "
              f"{classes[UNCLASSIFIED]} unclassified; the co-sign leg is evaluated over the "
              "rule-change slice only")
        print()
        print(renderer(rows))

    return 1 if (args.fail and counts[UNRECONCILED]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
