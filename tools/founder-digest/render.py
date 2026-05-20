"""HTML/PDF renderer for the daily Founder digest.

Renderer enforces three house-style guardrails at build time:
1. Plain-language layer (first-use gloss per page) via `glossary.PageGlosser`.
2. Body copy contains zero BEAAA-N ticket IDs — pulled into a footer index.
3. Anti-pattern phrase scrub + deprecated-term scrub (raises on hit).

Carrier Rule: source data passes through unchanged. The renderer does not
inject framing words about any carrier.
"""
from __future__ import annotations

import datetime as _dt
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Iterable

import sys as _sys
from pathlib import Path as _Path

_HERE = _Path(__file__).resolve().parent
if str(_HERE) not in _sys.path:
    _sys.path.insert(0, str(_HERE))

from fetcher import AgentLite, DigestState, Interaction, IssueLite
from glossary import (
    PageGlosser,
    TICKET_ID_PATTERN,
    canonicalize,
    find_anti_patterns,
    find_deprecated_terms,
    strip_ticket_ids,
)


SECTION_TITLES = {
    "approval": "Needs your decision",
    "blocked": "What is stuck and why",
    "yesterday": "What moved since yesterday",
    "ask": "One thing to do today",
}


def _prep_text(raw: str, ticket_collector: list["FooterEntry"], label: str) -> str:
    """Pipeline a free-text field for body copy:

    1. canonicalize deprecated codenames (BMI/BEAI/BEA -> canonical)
    2. strip BEAAA-N ticket ids, pushing them into the footer index
    3. tidy whitespace
    """
    text = canonicalize(raw)
    cleaned, ids = strip_ticket_ids(text)
    for tid in ids:
        ticket_collector.append(FooterEntry(label=label, ticket_id=tid))
    return cleaned


def _short_title(title: str, max_chars: int = 110) -> str:
    title = title.strip()
    if len(title) <= max_chars:
        return title
    return title[: max_chars - 1].rstrip() + "\u2026"


def _agent_label(agent_id: str | None, agents: dict[str, AgentLite]) -> str:
    if not agent_id:
        return "board / Founder"
    agent = agents.get(agent_id)
    if not agent:
        return "an unrecognized seat"
    return agent.label


def _format_age(now: _dt.datetime, then: _dt.datetime) -> str:
    delta = now - then
    days = int(delta.total_seconds() // 86400)
    if days <= 0:
        hours = max(1, int(delta.total_seconds() // 3600))
        return f"{hours}h"
    if days == 1:
        return "1 day"
    return f"{days} days"


@dataclass
class FooterEntry:
    label: str
    ticket_id: str


def _strip_for_body(text: str, ticket_collector: list[FooterEntry], label: str) -> str:
    """Deprecated wrapper kept for clarity; use _prep_text directly."""
    return _prep_text(text, ticket_collector, label)


def render_html(state: DigestState) -> tuple[str, list[FooterEntry], list[str]]:
    """Return (html, footer_entries, warnings)."""
    ticket_index: list[FooterEntry] = []
    warnings: list[str] = []

    glosser = PageGlosser()

    def gloss_text(raw: str) -> str:
        cleaned = _strip_for_body(raw, ticket_index, raw[:60])
        return glosser.apply(escape(cleaned))

    as_of_str = state.as_of.strftime("%A %d %B %Y %H:%M UTC")
    since_str = state.since.strftime("%A %d %B %Y %H:%M UTC")

    sections_html: list[str] = []

    sections_html.append(_render_approval(state, glosser, ticket_index))
    sections_html.append(_render_blocked(state, glosser, ticket_index))
    sections_html.append(_render_yesterday(state, glosser, ticket_index))
    sections_html.append(_render_founder_ask(state, glosser, ticket_index))

    footer_html = _render_footer_index(ticket_index)

    body_only = "\n".join(sections_html)
    anti_hits = find_anti_patterns(body_only)
    if anti_hits:
        warnings.append(f"anti-pattern phrases in body: {anti_hits}")
    dep_hits = find_deprecated_terms(body_only)
    if dep_hits:
        warnings.append(f"deprecated terms in body: {dep_hits}")

    if TICKET_ID_PATTERN.search(_html_to_text(body_only)):
        warnings.append("BEAAA-N ticket id leaked into body copy")

    html = _wrap_document(
        as_of_str=as_of_str,
        since_str=since_str,
        sections_html=body_only,
        footer_html=footer_html,
    )
    return html, ticket_index, warnings


def _render_approval(state: DigestState, glosser: PageGlosser,
                     ticket_index: list[FooterEntry]) -> str:
    glosser.reset()
    title = glosser.apply(escape(SECTION_TITLES["approval"]))

    items: list[str] = []
    for ix in state.approval_interactions:
        label = ix.issue_title.strip()[:80]
        ticket_index.append(FooterEntry(label=label, ticket_id=ix.issue_identifier))
        kind_text = "your decision needed" if ix.kind == "request_confirmation" \
            else "questions waiting for your answer"
        clean_title = _prep_text(ix.issue_title, ticket_index, label)
        prompt_text = _prep_text(ix.prompt or "(no prompt provided)", ticket_index, label)
        prompt_text = _truncate_sentences(prompt_text, max_sentences=2)
        items.append(
            f'<li class="approval">'
            f'<div class="ask-line">{glosser.apply(escape(kind_text))}: '
            f'{glosser.apply(escape(_short_title(clean_title)))}.</div>'
            f'<div class="ask-prompt">{glosser.apply(escape(prompt_text))}</div>'
            f'</li>'
        )

    for issue in state.approval_in_review:
        label = issue.title.strip()[:80]
        ticket_index.append(FooterEntry(label=label, ticket_id=issue.identifier))
        owner = _agent_label(issue.assignee_agent_id, state.agents_by_id)
        clean_title = _prep_text(issue.title, ticket_index, label)
        line = f"Waiting on you: {_short_title(clean_title)}. Routed from {owner}."
        items.append(
            f'<li class="approval">'
            f'<div class="ask-line">{glosser.apply(escape(line))}</div>'
            f'</li>'
        )

    if not items:
        items.append('<li class="approval none">Nothing waits on you right now.</li>')

    return f'''<section class="section approval-section">
  <h2>{title}</h2>
  <ol class="approval-list">{"".join(items)}</ol>
</section>'''


def _render_blocked(state: DigestState, glosser: PageGlosser,
                    ticket_index: list[FooterEntry]) -> str:
    glosser.reset()
    title = glosser.apply(escape(SECTION_TITLES["blocked"]))

    rows: list[str] = []
    for issue, cause in state.blocked_top10:
        label = issue.title.strip()[:80]
        ticket_index.append(FooterEntry(label=label, ticket_id=issue.identifier))
        age = _format_age(state.as_of, issue.last_activity_at)
        owner = _agent_label(issue.assignee_agent_id, state.agents_by_id)
        clean_title = _prep_text(issue.title, ticket_index, label)
        cause_text = _prep_text(cause, ticket_index, label)
        cause_text = _truncate_sentences(cause_text, max_sentences=1)
        rows.append(
            f'<li class="blocked-item">'
            f'<div class="bl-title">{glosser.apply(escape(_short_title(clean_title)))}'
            f' <span class="meta">— {glosser.apply(escape(owner))}, stuck {age}.</span></div>'
            f'<div class="bl-cause">{glosser.apply(escape(cause_text))}</div>'
            f'</li>'
        )
    if not rows:
        rows.append('<li class="blocked-item none">Nothing is stuck.</li>')

    return f'''<section class="section blocked-section">
  <h2>{title}</h2>
  <ol class="blocked-list">{"".join(rows)}</ol>
</section>'''


def _render_yesterday(state: DigestState, glosser: PageGlosser,
                      ticket_index: list[FooterEntry]) -> str:
    glosser.reset()
    title = glosser.apply(escape(SECTION_TITLES["yesterday"]))

    moved_done: list[str] = []
    for issue in state.yesterday_done:
        label = issue.title.strip()[:80]
        ticket_index.append(FooterEntry(label=label, ticket_id=issue.identifier))
        owner = _agent_label(issue.assignee_agent_id, state.agents_by_id)
        clean_title = _prep_text(issue.title, ticket_index, label)
        moved_done.append(
            f'<li><span class="dot done">●</span> '
            f'{glosser.apply(escape(_short_title(clean_title)))}'
            f' <span class="meta">— completed by {glosser.apply(escape(owner))}.</span></li>'
        )

    moved_started: list[str] = []
    for issue in state.yesterday_started:
        label = issue.title.strip()[:80]
        ticket_index.append(FooterEntry(label=label, ticket_id=issue.identifier))
        owner = _agent_label(issue.assignee_agent_id, state.agents_by_id)
        clean_title = _prep_text(issue.title, ticket_index, label)
        moved_started.append(
            f'<li><span class="dot started">●</span> '
            f'{glosser.apply(escape(_short_title(clean_title)))}'
            f' <span class="meta">— picked up by {glosser.apply(escape(owner))}.</span></li>'
        )

    body_parts = []
    if moved_done:
        body_parts.append(
            '<div class="subhead">Completed</div>'
            f'<ul class="moved">{"".join(moved_done)}</ul>'
        )
    if moved_started:
        body_parts.append(
            '<div class="subhead">Picked up</div>'
            f'<ul class="moved">{"".join(moved_started)}</ul>'
        )
    if not body_parts:
        body_parts.append('<p class="empty">Nothing moved in the last 24 hours.</p>')

    return f'''<section class="section yesterday-section">
  <h2>{title}</h2>
  {"".join(body_parts)}
</section>'''


def _render_founder_ask(state: DigestState, glosser: PageGlosser,
                        ticket_index: list[FooterEntry]) -> str:
    glosser.reset()
    title = glosser.apply(escape(SECTION_TITLES["ask"]))

    issue = state.founder_ask
    if issue is None:
        body = '<p class="empty">No single founder ask today.</p>'
    else:
        label = issue.title.strip()[:80]
        ticket_index.append(FooterEntry(label=label, ticket_id=issue.identifier))
        owner = _agent_label(issue.assignee_agent_id, state.agents_by_id)
        age = _format_age(state.as_of, issue.last_activity_at)
        reason = state.founder_ask_reason or "highest open priority"
        clean_title = _prep_text(issue.title, ticket_index, label)
        body = (
            f'<div class="ask-card">'
            f'<div class="ask-title">{glosser.apply(escape(_short_title(clean_title)))}</div>'
            f'<div class="ask-meta">Routed from {glosser.apply(escape(owner))}.'
            f' Open {age}. Reason: {glosser.apply(escape(reason))}.</div>'
            f'</div>'
        )

    return f'''<section class="section founder-ask-section">
  <h2>{title}</h2>
  {body}
</section>'''


def _render_footer_index(entries: list[FooterEntry]) -> str:
    if not entries:
        return ""
    seen: dict[str, str] = {}
    for e in entries:
        if e.ticket_id not in seen:
            seen[e.ticket_id] = e.label
    rows = "".join(
        f'<li><span class="ftk">{escape(tid)}</span> {escape(label)}</li>'
        for tid, label in sorted(seen.items(), key=lambda kv: _ticket_sort(kv[0]))
    )
    return f'<section class="ticket-index"><h3>Reference index</h3><ul>{rows}</ul></section>'


def _ticket_sort(tid: str) -> tuple[str, int]:
    try:
        prefix, num = tid.split("-", 1)
        return (prefix, int(num))
    except Exception:
        return (tid, 0)


def _truncate_sentences(text: str, max_sentences: int = 2) -> str:
    import re
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    out = " ".join(sentences[:max_sentences]).strip()
    return out


def _html_to_text(html: str) -> str:
    import re
    return re.sub(r"<[^>]+>", " ", html)


def _wrap_document(*, as_of_str: str, since_str: str,
                   sections_html: str, footer_html: str) -> str:
    css = _css()
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Daily Founder digest</title>
<style>{css}</style>
</head>
<body>
<header class="doc-header">
  <div class="title-row">
    <div class="brand">Founder digest</div>
    <div class="snapshot">Snapshot as of {escape(as_of_str)}</div>
  </div>
  <div class="since">Comparing against {escape(since_str)}.</div>
</header>
<main>
{sections_html}
</main>
<footer class="doc-footer">
  {footer_html}
  <div class="boilerplate">Plain-language layer applied on first use per page. Canonical state remains the source of truth.</div>
</footer>
</body>
</html>
"""


def _css() -> str:
    return """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  font-size: 14px; line-height: 1.55; color: #14181f; background: #ffffff;
  padding: 28px 36px 40px;
}
.doc-header { border-bottom: 2px solid #1a2027; padding-bottom: 12px; margin-bottom: 22px; }
.title-row { display: flex; justify-content: space-between; align-items: baseline; gap: 16px; }
.brand { font-size: 20px; font-weight: 700; letter-spacing: -0.01em; }
.snapshot { font-size: 12px; color: #4a5360; }
.since { font-size: 12px; color: #4a5360; margin-top: 4px; }

main .section { margin-bottom: 26px; page-break-inside: avoid; }
main h2 { font-size: 16px; font-weight: 700; color: #14181f; margin-bottom: 10px;
  border-left: 4px solid #1a2027; padding-left: 8px; }

ol, ul { padding-left: 22px; }
li { margin-bottom: 8px; }

.approval-list .ask-line { font-weight: 600; }
.approval-list .ask-prompt { color: #2c333d; font-size: 13px; margin-top: 2px; }

.blocked-list .bl-title { font-weight: 600; }
.blocked-list .bl-cause { color: #2c333d; font-size: 13px; margin-top: 2px; }
.blocked-list .meta, .yesterday-section .meta { color: #6b7380; font-weight: 400; }

.subhead { font-weight: 600; font-size: 13px; margin-top: 8px; color: #1a2027; }
.moved { list-style: none; padding-left: 4px; }
.moved li { display: flex; gap: 8px; align-items: baseline; }
.dot.done { color: #2e7d32; }
.dot.started { color: #1565c0; }

.ask-card {
  border: 1px solid #cdd5e0; border-left: 4px solid #c67c00;
  background: #fff8ee; padding: 12px 14px; border-radius: 4px;
}
.ask-title { font-weight: 700; font-size: 15px; }
.ask-meta { color: #4a5360; font-size: 12px; margin-top: 6px; }

.empty { color: #6b7380; font-style: italic; }
.none { color: #6b7380; font-style: italic; }

.doc-footer { margin-top: 28px; padding-top: 12px; border-top: 1px solid #cdd5e0; font-size: 11px; color: #4a5360; }
.ticket-index h3 { font-size: 12px; font-weight: 700; color: #1a2027; margin-bottom: 6px; }
.ticket-index ul { list-style: none; padding-left: 0; column-count: 2; column-gap: 24px; }
.ticket-index li { margin-bottom: 4px; break-inside: avoid; }
.ftk { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-weight: 600; color: #1a2027; }
.boilerplate { margin-top: 10px; color: #6b7380; }
"""


_PDF_DATE_RE = __import__("re").compile(rb"\(D:\d{14}\+\d{2}'\d{2}'\)")


def _normalize_pdf_dates(pdf_path: Path, source_date_epoch: int) -> None:
    """Rewrite /CreationDate and /ModDate to a deterministic value.

    Chrome's headless PDF embeds wall-clock CreationDate/ModDate regardless
    of SOURCE_DATE_EPOCH. Without normalisation, two runs against identical
    state produce binary-different PDFs. We post-process the file in place,
    rewriting any `(D:YYYYMMDDHHMMSS+00'00')` token to the as-of epoch.
    """
    import datetime as _dt
    dt = _dt.datetime.fromtimestamp(source_date_epoch, tz=_dt.timezone.utc)
    stamp = dt.strftime("%Y%m%d%H%M%S")
    replacement = f"(D:{stamp}+00'00')".encode("ascii")
    data = pdf_path.read_bytes()
    new = _PDF_DATE_RE.sub(replacement, data)
    if new != data:
        pdf_path.write_bytes(new)


def render_pdf(html_path: Path, pdf_path: Path,
               source_date_epoch: int | None = None,
               timeout_s: int = 90) -> tuple[int, str]:
    """Invoke headless Chrome to produce a PDF. Returns (returncode, stderr)."""
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "google-chrome", "--headless=new",
        "--disable-gpu", "--no-sandbox",
        "--no-pdf-header-footer",
        f"--print-to-pdf={pdf_path.resolve()}",
        f"file://{html_path.resolve()}",
    ]
    env = os.environ.copy()
    if source_date_epoch is not None:
        env["SOURCE_DATE_EPOCH"] = str(source_date_epoch)
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout_s, env=env,
    )
    if proc.returncode == 0 and pdf_path.exists() and source_date_epoch is not None:
        _normalize_pdf_dates(pdf_path, source_date_epoch)
    return proc.returncode, proc.stderr
