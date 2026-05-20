"""Acceptance-criteria verification for a rendered Founder digest PDF.

Usage:
    python3 tools/founder-digest/verify.py path/to/digest.pdf
    python3 tools/founder-digest/verify.py path/a.pdf path/b.pdf  # determinism

Checks AC #3 (zero BEAAA-N ids in body copy, footer index allowed) and
AC #4 (zero raw canonical terms outside their first-use gloss).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _extract_text(pdf_path: Path) -> str:
    import pypdf
    reader = pypdf.PdfReader(str(pdf_path))
    return "\n".join(p.extract_text() or "" for p in reader.pages)


def _split_body(text: str) -> tuple[str, str]:
    parts = text.split("Reference index", 1)
    body = parts[0]
    footer = parts[1] if len(parts) > 1 else ""
    return body, footer


_GLOSS_PATTERNS = (
    re.compile(r"our\s+agent\s+risk\s+scanner\s*\(\s*ARE\s+Scanner\s*\)", re.DOTALL),
    re.compile(r"our\s+agent\s+risk\s+score\s*\(\s*ARE\s*\)", re.DOTALL),
    re.compile(r"us\s*\(\s*IFA\s*\)", re.DOTALL),
)


def _gloss_spans(body: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for p in _GLOSS_PATTERNS:
        for m in p.finditer(body):
            spans.append((m.start(), m.end()))
    spans.sort()
    return spans


def _in_any_span(pos: int, spans: list[tuple[int, int]]) -> bool:
    for s, e in spans:
        if s <= pos < e:
            return True
    return False


def check_pdf(pdf_path: Path) -> dict[str, object]:
    text = _extract_text(pdf_path)
    body, footer = _split_body(text)
    body_ids = re.findall(r"\bBEAAA-\d+\b", body)
    footer_ids = re.findall(r"\bBEAAA-\d+\b", footer)
    gloss_spans = _gloss_spans(body)

    raw_canonicals: dict[str, int] = {}
    for term in ("ARE Scanner", "ARE", "IFA", "Phase 1"):
        positions = [m.start() for m in re.finditer(rf"\b{re.escape(term)}\b", body)]
        raw = sum(1 for p in positions if not _in_any_span(p, gloss_spans))
        raw_canonicals[term] = raw

    deprecated = {
        t: len(re.findall(rf"\b{t}\b", body)) for t in ("BMI", "ARI", "BEAI", "BEA")
    }

    return {
        "pdf": str(pdf_path),
        "size_bytes": pdf_path.stat().st_size,
        "body_ticket_ids": body_ids,
        "footer_ticket_ids_count": len(footer_ids),
        "raw_canonicals_outside_gloss": raw_canonicals,
        "deprecated_terms": deprecated,
    }


def determinism_diff(a: Path, b: Path) -> dict[str, object]:
    return {
        "a": str(a), "b": str(b),
        "size_a": a.stat().st_size, "size_b": b.stat().st_size,
        "byte_stable": a.read_bytes() == b.read_bytes(),
    }


def main(argv: list[str]) -> int:
    if len(argv) == 1:
        report = check_pdf(Path(argv[0]))
    elif len(argv) == 2:
        report = {
            "first": check_pdf(Path(argv[0])),
            "second": check_pdf(Path(argv[1])),
            "determinism": determinism_diff(Path(argv[0]), Path(argv[1])),
        }
    else:
        print("usage: verify.py PDF [PDF2]", file=sys.stderr)
        return 2

    import json
    print(json.dumps(report, indent=2, default=str))

    failures: list[str] = []
    flat = report if "first" not in report else report["first"]
    if isinstance(flat, dict):
        if flat.get("body_ticket_ids"):
            failures.append(f"AC#3 fail: ticket ids in body: {flat['body_ticket_ids']}")
        raw = flat.get("raw_canonicals_outside_gloss", {})
        for term, n in raw.items():
            if n:
                failures.append(f"AC#4 fail: {term} appears raw {n} times outside gloss")
        for term, n in flat.get("deprecated_terms", {}).items():
            if n:
                failures.append(f"deprecated term {term} present in body: {n}")
    if "determinism" in report and not report["determinism"]["byte_stable"]:
        failures.append("determinism fail: PDFs differ byte-wise")

    if failures:
        print("FAILURES:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
