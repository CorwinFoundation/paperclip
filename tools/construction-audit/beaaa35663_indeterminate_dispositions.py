#!/usr/bin/env python3
"""BEAAA-35663: give every unreadable production-scored row a named disposition.

BEAAA-35660 audited 479 production-scored rows against the five retired-construction
signatures. 96 came back indeterminate - no target-channel construction text could be
read. BEAAA-35661 recovered 54 of them by fixing the extractor's key matcher (it was an
anchored exact alternation, so ``prompt_text`` matched neither ``prompt`` nor ``text``)
and by following the route a descriptor pack declares in its own ``runner`` field.

42 remain. "Indeterminate" on its own is not a disposition: it does not say whether the
text is missing, elsewhere, or absent by construction, and it does not say what reading
it would cost. This script assigns each remaining row to one of four classes keyed on
the pack it comes from, so the reason is derived from the ledger rather than typed, and
records for each class the path that holds the construction and the concrete step that
would make it readable.

None of this moves a score. The gate already refuses these rows; this records why.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LEDGER = REPO / "artifacts/beaaa-35660/construction_ledger.json"
OUT_JSON = REPO / "artifacts/beaaa-35661/indeterminate_dispositions.json"
OUT_MD = REPO / "artifacts/beaaa-35661/indeterminate_dispositions.md"

#: pack path -> disposition class. Keyed on the pack because the reason a row cannot be
#: read is a property of the pack's shape, not of the family label sitting on the row.
PACK_CLASS = {
    "rubric/probe_mappings/garak.yaml": "upstream_library_generates_prompts_at_dispatch",
    "rubric/probe_mappings/garak_beaaa152.yaml": "upstream_library_generates_prompts_at_dispatch",
    "rubric/probe_mappings/agentdojo.yaml": "upstream_library_generates_prompts_at_dispatch",
    "rubric/probe_mappings/jailbreakbench.yaml": "aggregate_category_row_over_upstream_dataset",
    "rubric/probe_mappings/advbench.yaml": "aggregate_category_row_over_upstream_dataset",
    "rubric/probe_mappings/factscore.yaml": "aggregate_category_row_over_upstream_dataset",
    "rubric/probe_mappings/halueval.yaml": "aggregate_category_row_over_upstream_dataset",
    "rubric/probe_mappings/promptinject.yaml": "aggregate_category_row_over_upstream_dataset",
    "rubric/probe_mappings/eval_representation.yaml": "no_model_facing_prompt_by_construction",
    "rubric/probe_mappings/pii_attestation.yaml": "no_model_facing_prompt_by_construction",
}

CLASS_DETAIL = {
    "upstream_library_generates_prompts_at_dispatch": {
        "reason": (
            "The row names an upstream probe class (garak probe_id, AgentDojo task suite), not a "
            "prompt. The text is constructed inside the third-party package when the wrapper "
            "dispatches it; the pack carries probe_id, pillar, dim and weight and no prompt text. "
            "Neither package is vendored in this tree, so there is nothing in-repo to read."
        ),
        "path_that_would_hold_it": (
            "the installed third-party package (garak probe modules / agentdojo task suites) at the "
            "version the wrapper resolves at run time"
        ),
        "what_it_would_take": (
            "Pin the upstream version, snapshot each named probe class's emitted prompt list into a "
            "vendored corpus under tests/probe_sets/, and have the wrapper record a per-case text "
            "hash at execution. Size and licence of the vendored snapshot is a decision above this "
            "issue, so it is filed as its own row rather than done here."
        ),
    },
    "aggregate_category_row_over_upstream_dataset": {
        "reason": (
            "The row is one dataset category, not one case: probe_id jbb.<category> / "
            "advbench.<slice> / halueval.<split> with a category label and a weight and no case "
            "list. Its members are pulled from the upstream dataset by the wrapper at run time, so "
            "no single construction text exists to read at the row's granularity."
        ),
        "path_that_would_hold_it": (
            "the upstream dataset the wrapper fetches, plus the in-tree per-case corpora that cover "
            "part of the same ground (tests/probe_sets/p5_direct_advbench_v1.jsonl, "
            "p5_direct_jailbreakbench_v1.jsonl, p4_grounded_factuality_halueval_qa_v1.jsonl, "
            "p4_citation_grounded_halueval_summ_v1.jsonl - whose per-case registry rows read clear)"
        ),
        "what_it_would_take": (
            "Define an aggregate row's construction as the union of its member cases and require the "
            "wrapper to emit a per-case manifest at execution, then run the predicate over the "
            "manifest instead of over the category row. That is a change to what the wrapper writes, "
            "not a change to the predicate."
        ),
    },
    "no_model_facing_prompt_by_construction": {
        "reason": (
            "The row has no target channel at all. eval_representation.er.derived_coverage is derived "
            "from other rows' coverage and pii_attestation.evidence_pack is a governance attestation "
            "over an evidence pack. Neither sends text to a model, so the question the predicate asks "
            "does not apply to them."
        ),
        "path_that_would_hold_it": "not applicable - no prompt is ever constructed",
        "what_it_would_take": (
            "A not_applicable verdict class for non-prompt rows, so they stop being counted as "
            "unread. That widens the predicate's vocabulary and is a proposal on BEAAA-35661, not a "
            "silent change here: until it exists these two rows stay indeterminate and the gate keeps "
            "refusing them, which is the conservative direction."
        ),
    },
    "unclassified_pack": {
        "reason": "The pack is not in PACK_CLASS, so no reason has been recorded for it yet.",
        "path_that_would_hold_it": "unknown",
        "what_it_would_take": "Classify the pack here before this row can be called closed.",
    },
}


def build() -> dict:
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    rows = [r for r in ledger["rows"] if r.get("scoring_active")]
    verdicts: dict[str, int] = {}
    for row in rows:
        verdicts[row["verdict"]] = verdicts.get(row["verdict"], 0) + 1
    indeterminate = [r for r in rows if r["verdict"] == "indeterminate"]

    classes: dict[str, dict] = {}
    for row in indeterminate:
        pack = row.get("source_pack") or ""
        name = PACK_CLASS.get(pack, "unclassified_pack")
        entry = classes.setdefault(name, {**CLASS_DETAIL[name], "rows": 0, "packs": {}, "cases": []})
        entry["rows"] += 1
        entry["packs"][pack] = entry["packs"].get(pack, 0) + 1
        entry["cases"].append({
            "case_id": row["case_id"],
            "family": row.get("family"),
            "rubric_point": row.get("rubric_point"),
            "runner": row.get("runner"),
            "source_pack": pack,
        })
    for entry in classes.values():
        entry["cases"].sort(key=lambda c: c["case_id"])
        entry["packs"] = dict(sorted(entry["packs"].items()))

    return {
        "schema": "backbond-indeterminate-disposition@1",
        "issue": "BEAAA-35663",
        "parent": "BEAAA-35661",
        "derived_from": "artifacts/beaaa-35660/construction_ledger.json",
        "produced_by": "tools/construction-audit/beaaa35663_indeterminate_dispositions.py",
        "audited_at_commit": ledger.get("rows_audited") and None or None,
        "production_scored_rows": len(rows),
        "verdicts": dict(sorted(verdicts.items())),
        "opening_indeterminate_count_beaaa_35660": 96,
        "recovered_readable_beaaa_35661": 96 - len(indeterminate),
        "unreadable_with_reason": len(indeterminate),
        "classes": dict(sorted(classes.items())),
    }


def render(payload: dict) -> str:
    lines = [
        "# BEAAA-35663 - disposition of the production-scored rows that stayed unreadable",
        "",
        f"Production-scored rows audited: {payload['production_scored_rows']}. "
        f"Verdicts: {payload['verdicts']}.",
        "",
        f"BEAAA-35660 opened with {payload['opening_indeterminate_count_beaaa_35660']} indeterminate "
        f"rows. {payload['recovered_readable_beaaa_35661']} became readable once the extractor's key "
        f"matcher and the descriptor-pack runner route were fixed. "
        f"{payload['unreadable_with_reason']} remain, each in one of the classes below. No row is "
        "flagged.",
        "",
        "| class | rows | packs | what it would take |",
        "| --- | --- | --- | --- |",
    ]
    for name, entry in payload["classes"].items():
        packs = "<br>".join(f"`{p}` ({n})" for p, n in entry["packs"].items())
        lines.append(f"| {name} | {entry['rows']} | {packs} | {entry['what_it_would_take']} |")
    for name, entry in payload["classes"].items():
        lines += [
            "",
            f"## {name} ({entry['rows']} rows)",
            "",
            entry["reason"],
            "",
            f"Construction lives at: {entry['path_that_would_hold_it']}",
            "",
            "Rows: " + ", ".join(f"`{c['case_id']}`" for c in entry["cases"]),
        ]
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build()
    payload.pop("audited_at_commit", None)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    OUT_MD.write_text(render(payload), encoding="utf-8")
    print(f"production_scored={payload['production_scored_rows']} verdicts={payload['verdicts']}")
    print(f"recovered={payload['recovered_readable_beaaa_35661']} "
          f"unreadable_with_reason={payload['unreadable_with_reason']}")
    for name, entry in payload["classes"].items():
        print(f"  {name}: {entry['rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
