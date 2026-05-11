"""P1.5 dry-run on the LawConnect Atticus surface — BEAAA-208 acceptance bullet.

We do NOT hit the live Atticus endpoint for this dry-run. The Atticus
questions-stage surface architecturally does not answer factual prompts —
it generates a structured follow-up question instead, and never emits a
self-reported confidence float. Driving it with the P1.5 corpus is
expected to produce na_rate = 1.0 -> coverage_label = "coverage-not-scored".

This script proves the **P1.5 cell-emission shape is correct** on a
workflow-restricted surface profile (Atticus-style follow-up-question
behavior), without burning production-traffic etiquette or budget. The
artifact captures the P1.5 cell exactly as it would appear if the
existing scripts/scan_lawconnect_atticus.py path were extended to emit
a P1.5 row.

Sister script: scripts/scan_lawconnect_atticus.py drives the live
Atticus surface for P1.4 + P3.1. P1.5 is held back from the live path
in v0.1.0 because (a) the cell will always be coverage-not-scored on
this surface and (b) the live scan would burn ~100 extra POSTs per run
with no incremental information content — UW co-sign required if we
want to emit the cell on the live path anyway, for completeness.

Usage:
    python3 scripts/dry_run_p1_5_atticus.py [--limit N] [--out-dir PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.bench import confidence_calibration as cc  # noqa: E402
from harness.runner import MockRunner  # noqa: E402


def _atticus_style_responder(prompt: str) -> str:
    """Mimic the Atticus questions-stage LLM behavior.

    The surface generates a structured follow-up question regardless of
    the prompt content. It does NOT answer the prompt, and it does NOT
    emit a self-reported confidence float. P1.5 elicitation is therefore
    expected to be un-parseable on every item.
    """
    # Deterministic, content-free follow-up question. Identical to the
    # shape the live surface returns on free-text legal-matter inputs.
    return (
        "Thanks — to help match you with the right firm, could you tell me "
        "more about the parties involved and roughly when this issue "
        "started? You can pick: a) within the last week, b) within the "
        "last month, c) more than a month ago."
    )


def _well_behaved_responder(prompt: str) -> str:
    """A reference responder that DOES emit a confidence and answer correctly.

    Used as a side-by-side reference so the artifact shows both shapes —
    coverage-not-scored vs measured — on the same corpus.
    """
    # We grade the response against the corpus's accept substrings; the
    # responder needs to actually return one of them. We can cheat by
    # peeking at the item via prompt-prefix; this is the same idiom used
    # in tests/test_p1_expansion.py.
    items = cc.load_corpus()
    for item in items:
        if prompt.startswith(item.prompt):
            first_accept = (item.gold or "").split(",")[0].strip() or "I am not sure"
            # Confidence band aligned to difficulty so the curve has spread.
            conf = {"easy": 0.95, "medium": 0.75, "hard": 0.55}.get(item.difficulty, 0.7)
            return f"{first_accept}\n\n{conf}"
    return "I am not sure.\n0.3"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None,
                        help="cap corpus to N items (smoke). Default: all 100.")
    parser.add_argument("--out-dir", default="artifacts/beaaa-208",
                        help="artifact output directory")
    args = parser.parse_args()

    items = cc.load_corpus(limit=args.limit)
    out_dir = REPO / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    scored_at = datetime(2026, 5, 11, 0, 0, 0, tzinfo=timezone.utc)
    corpus_v = cc.corpus_version()

    # 1) Atticus-style surface — expected coverage-not-scored.
    atticus = MockRunner("lawconnect-atticus-questions-stage:mock",
                         _atticus_style_responder)
    atticus_result = cc.run(atticus, items)
    # Strip the verbose trace from the artifact summary; keep a small sample.
    atticus_summary = {k: v for k, v in atticus_result.items() if k != "trace"}
    atticus_summary["trace_sample"] = atticus_result["trace"][:3]

    # 2) Well-behaved reference — should produce a measured cell.
    well = MockRunner("reference-well-behaved:mock", _well_behaved_responder)
    well_result = cc.run(well, items)
    well_summary = {k: v for k, v in well_result.items() if k != "trace"}
    well_summary["trace_sample"] = well_result["trace"][:3]

    artifact = {
        "issue": "BEAAA-208",
        "purpose": "P1.5 cell-emission dry-run on the Atticus questions-stage surface profile",
        "scored_at": scored_at.isoformat(),
        "corpus_version": corpus_v,
        "scanner_version": "0.2.3",  # matches the live atticus scan script
        "n_items": len(items),
        "surfaces": {
            "lawconnect-atticus-questions-stage": atticus_summary,
            "reference-well-behaved": well_summary,
        },
        "notes": [
            "The Atticus surface emits structured follow-up questions, not answers, "
            "and never includes a self-reported confidence float. P1.5 elicitation "
            "is therefore architecturally un-parseable on this surface; coverage_label "
            "MUST be 'coverage-not-scored' (not raw 0).",
            "The reference-well-behaved surface is included as a side-by-side shape "
            "check: same corpus, alternative surface behavior, measured cell.",
            "Live Atticus scan is NOT touched by this dry-run.",
        ],
    }
    out = out_dir / "p1_5_atticus_dry_run.json"
    out.write_text(json.dumps(artifact, indent=2))

    # Contract assertions — these must hold for the BEAAA-208 acceptance.
    assert atticus_result["coverage_label"] == "coverage-not-scored", (
        f"Atticus surface must emit coverage-not-scored on P1.5 — got "
        f"{atticus_result['coverage_label']!r}"
    )
    assert atticus_result["na_rate"] == 1.0, (
        f"Atticus surface must produce na_rate=1.0 on P1.5 — got "
        f"{atticus_result['na_rate']!r}"
    )
    assert well_result["coverage_label"] == "measured", (
        f"reference well-behaved surface must emit measured on P1.5 — got "
        f"{well_result['coverage_label']!r}"
    )

    print(f"# P1.5 dry-run — BEAAA-208")
    print(f"Corpus version: {corpus_v} (n={len(items)})")
    print()
    print("| Surface | coverage_label | na_rate | raw | ece | brier |")
    print("|---|---|---:|---:|---:|---:|")
    def _fmt(x: float | None, places: int) -> str:
        return f"{x:.{places}f}" if isinstance(x, (int, float)) else "—"

    for label, res in (("lawconnect-atticus", atticus_result),
                       ("reference-well-behaved", well_result)):
        print(
            f"| {label} | {res['coverage_label']} | "
            f"{res['na_rate']:.2f} | {_fmt(res['raw'], 2)} | "
            f"{_fmt(res['ece'], 4)} | {_fmt(res['brier'], 4)} |"
        )
    print()
    print(f"_Artifact: {out}_")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
