"""AILuminate v1.0 DEMO corpus loader — BEAAA-218 sanity-load scaffold.

MLCommons AI Risk & Reliability working group "AILuminate" benchmark,
DEMO subset (1,200 public practice prompts, 10% sample of the
12,000-prompt Public Practice dataset). Released under CC-BY-4.0 by
MLCommons — see harness/fixtures/AILUMINATE_DEMO_NOTICE.md for the
attribution + warning text reproduced from the upstream README.

Source: github.com/mlcommons/ailuminate
File:   airr_official_1.0_demo_en_us_prompt_set_release.csv
Commit: pinned via the SHA in BEAAA-218 (rev attestation lives in the
        scanner's release notes; the demo set is content-stable per
        MLCommons release cadence).

Scope here (BEAAA-218 phase-1):
- Pure corpus loader. NO scoring, NO dim mapping wired in yet —
  AILuminate-derived measurements stay OFF the ARE rubric until the
  v0.2 wiring task (BEAAA-201 follow-up) co-signs a hazard → ARE
  pillar/dim mapping with UW. That gates rubric impact + preserves
  BEAAA-21 byte-stability for v1.0.
- The loader emits items in the same per-item shape canonical benches
  use (id, prompt, plus AILuminate-specific metadata) so downstream
  task design has a single integration surface.

Content warning: AILuminate prompts are written to elicit hazardous
responses across 12 hazard categories (CSAM, weapons, hate, self-harm,
etc.). Do not echo prompts unredacted into reports or external comms.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "ailuminate_demo_en_us_v1.0.csv"
)

# AILuminate v1.0 hazard taxonomy as encoded in the DEMO release CSV.
# Upstream README documents 12 top-level categories; the released file
# splits "specialized advice" into 4 sub-codes (elections / financial /
# health / legal), so the observed code-set is 15. Reproduced verbatim
# from `airr_official_1.0_demo_en_us_prompt_set_release.csv`.
HAZARD_CATEGORIES: frozenset[str] = frozenset(
    {
        "vcr",  # violent crimes
        "ncr",  # non-violent crimes
        "src",  # sex-related crimes
        "cse",  # child sexual exploitation
        "ssh",  # suicide & self-harm
        "iwp",  # indiscriminate weapons of mass destruction
        "hte",  # hate
        "prv",  # privacy
        "ipv",  # intellectual property
        "dfm",  # defamation
        "spc_ele",  # specialized advice — elections
        "spc_fin",  # specialized advice — financial
        "spc_hlt",  # specialized advice — health
        "spc_lgl",  # specialized advice — legal
        "sxc_prn",  # sexual content — porn (NSFW)
    }
)

# Persona modifiers used by AILuminate v1.0 — these stratify prompts by
# the simulated user role (skilled adversary, unskilled adversary, etc).
PERSONAS: frozenset[str] = frozenset(
    {
        "normal",
        "skilled",
        "unskilled",
    }
)


@dataclass(frozen=True)
class AILuminateItem:
    """One AILuminate v1.0 DEMO prompt row.

    `release_prompt_id` is MLCommons' canonical identifier; we keep it
    verbatim so cross-vendor reproducibility is preserved.
    """

    release_prompt_id: str
    prompt_text: str
    hazard: str
    persona: str
    locale: str
    prompt_hash: str


def load_items(
    fixture_path: str | Path = DEFAULT_FIXTURE,
    *,
    hazard_filter: Iterable[str] | None = None,
    persona_filter: Iterable[str] | None = None,
    limit: int | None = None,
) -> list[AILuminateItem]:
    """Load the AILuminate DEMO CSV. Filters are inclusive (set membership).

    `hazard_filter=None` → all 12 hazard categories. Pass e.g.
    `{"hte", "prv"}` to restrict to hate + privacy.
    `persona_filter=None` → all personas.
    `limit` → cap items returned (useful for smoke runs without paying
    a full 1,200-prompt sweep).
    """
    path = Path(fixture_path)
    if not path.exists():
        raise FileNotFoundError(
            f"AILuminate DEMO fixture not found at {path}. "
            "See harness/bench/ailuminate.py docstring for the source URL."
        )

    hazard_set = frozenset(hazard_filter) if hazard_filter else None
    persona_set = frozenset(persona_filter) if persona_filter else None

    items: list[AILuminateItem] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        _validate_header(reader.fieldnames)
        for row in reader:
            if hazard_set is not None and row["hazard"] not in hazard_set:
                continue
            if persona_set is not None and row["persona"] not in persona_set:
                continue
            items.append(
                AILuminateItem(
                    release_prompt_id=row["release_prompt_id"],
                    prompt_text=row["prompt_text"],
                    hazard=row["hazard"],
                    persona=row["persona"],
                    locale=row["locale"],
                    prompt_hash=row["prompt_hash"],
                )
            )
            if limit is not None and len(items) >= limit:
                break
    return items


_REQUIRED_HEADER = [
    "release_prompt_id",
    "prompt_text",
    "hazard",
    "persona",
    "locale",
    "prompt_hash",
]


def _validate_header(fieldnames: Iterable[str] | None) -> None:
    if fieldnames is None:
        raise ValueError("AILuminate CSV is missing a header row")
    actual = list(fieldnames)
    if actual != _REQUIRED_HEADER:
        raise ValueError(
            "AILuminate CSV header drift: "
            f"expected {_REQUIRED_HEADER}, got {actual}. "
            "Schema change in upstream release — re-pin and update loader."
        )


def hazard_histogram(items: Iterable[AILuminateItem]) -> dict[str, int]:
    """Per-hazard count — used by smoke/sanity tests to confirm the
    full taxonomy is loaded."""
    out: dict[str, int] = {}
    for it in items:
        out[it.hazard] = out.get(it.hazard, 0) + 1
    return out


__all__ = [
    "AILuminateItem",
    "DEFAULT_FIXTURE",
    "HAZARD_CATEGORIES",
    "PERSONAS",
    "hazard_histogram",
    "load_items",
]
