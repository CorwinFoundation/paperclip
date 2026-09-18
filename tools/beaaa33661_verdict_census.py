"""BEAAA-33661: is the signed run manifest or the captured evidence file the verdict of record?

``build_ledger`` reads the verdict from ``artifacts/beaaa-31092/rank2-run-manifest.json``
and the response from the evidence file that manifest row names.  On the pinned
corpus the two disagree on 75 of the 160 ``content_candidate`` rows.  This module
recomputes that census from the two artefacts and buckets every disagreement by
cause, so the authority question is answered from counts rather than narrative.

The CEO's first ask was to rule out the read path before blaming the data: the
module's one case-sensitive verdict read could misparse an uppercase ``FAIL`` and
manufacture a divergence.  ``casefold_only`` is that hypothesis expressed as a
number - the rows that agree after casefolding and disagree before it.  It is 0.

Run against the exported evidence root:

    python3 tools/beaaa33661_verdict_census.py \
        --evidence-root /var/lib/are-scanner/evidence/<export>/<job> \
        --out artifacts/beaaa-33661/verdict-census.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

MANIFEST = Path("artifacts/beaaa-31092/rank2-run-manifest.json")
LEDGER = Path("artifacts/beaaa-31092/rank2-ledger.json")

# The manifest's verdict, its numeric_result and the evidence file's score are
# three writes of one result.  A row whose verdict does not follow its own numeric
# field is not a disagreement with the other side - it is a row at war with itself.
NUMERIC_TO_VERDICT = {0.0: "fail", 1.0: "pass", None: "unresolved"}

ABSENT = "<absent>"


def _verdict(value: Any) -> str:
    return ABSENT if value is None else str(value)


def _bucket(manifest_row: dict[str, Any], record: dict[str, Any]) -> str:
    """Why this row's two verdicts differ.  Counts per cause, not a narrative."""
    if manifest_row.get("status") != "complete":
        # The manifest names these in its own ``incomplete_reasons`` as
        # ``required_case_not_attempted``.  It carries no verdict to defend.
        return "manifest_row_truncated_no_verdict"
    if not (record.get("responses") or []) and manifest_row.get("target_calls", 0) < 1:
        # Zero target calls, zero captured responses, null numeric_result - and the
        # manifest still stamps a literal verdict.  No measurement exists on either
        # side; the manifest's verdict is a default, not an observation.
        return "manifest_verdict_defaulted_on_unmeasured_case"
    return "result_triple_flip"


def _dispositions(ledger_path: Path) -> dict[str, str]:
    """evidence_ref -> the ledger's disposition for that row.

    The 75 is not the number that decides whether the 43/144 split has to move;
    the number that decides it is how many of the 43 *scored* rows sit inside the
    75.  ``evidence_ref`` is the only key the two artefacts share - the ledger's
    ``stable_case_id`` is a roster name, not the manifest's ``probe_id``.  The 27
    ledger rows carrying no ``evidence_ref`` are all held for
    ``missing_manifest_evidence`` and cannot be in a disagreement set that is
    defined by having a file to disagree with.
    """
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    return {
        str(row["evidence_ref"]): str(row.get("disposition"))
        for row in ledger.get("rows", [])
        if row.get("evidence_ref")
    }


def census(
    evidence_root: Path,
    manifest_path: Path = MANIFEST,
    ledger_path: Path = LEDGER,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dispositions = _dispositions(ledger_path)
    by_disposition: Counter[str] = Counter()
    scored_causes: Counter[str] = Counter()
    buckets: Counter[str] = Counter()
    pairs: Counter[str] = Counter()
    by_framework: Counter[str] = Counter()
    self_consistency: Counter[str] = Counter()
    casefold_only = 0
    misbound: Counter[str] = Counter()
    unreadable = 0

    for row in manifest.get("evidence_rows", []):
        framework = str(row.get("framework") or "?").split("@")[0]
        relative = str(row.get("evidence_ref") or "").removeprefix("evidence://").partition("/")[2]
        source = evidence_root / relative
        if not relative or not source.is_file():
            unreadable += 1
            continue
        record = json.loads(source.read_text(encoding="utf-8"))

        manifest_verdict = _verdict(row.get("verdict"))
        file_verdict = _verdict(record.get("verdict"))
        agrees = manifest_verdict == file_verdict
        agrees_folded = manifest_verdict.casefold() == file_verdict.casefold()
        if agrees_folded and not agrees:
            casefold_only += 1

        # A manifest row is bound to the right file when the probe id it carries is
        # the runtime case identity the file computed for itself.
        if str(row.get("probe_id")) != str(record.get("runtime_case_identity")):
            # promptfoo names its cases ``cac-NN`` and computes no runtime case
            # identity, so it is expected to miss here; content_candidate is not.
            misbound[framework] += 1

        if NUMERIC_TO_VERDICT.get(row.get("numeric_result"), "?") != manifest_verdict.casefold():
            self_consistency[f"{framework}:manifest_row_disagrees_with_its_own_numeric_result"] += 1
        if NUMERIC_TO_VERDICT.get(record.get("score"), "?") != file_verdict.casefold():
            self_consistency[f"{framework}:evidence_file_disagrees_with_its_own_score"] += 1

        disposition = dispositions.get(str(row.get("evidence_ref")), "<not_in_ledger>")
        by_framework[f"{framework}:{'agree' if agrees_folded else 'disagree'}"] += 1
        by_disposition[f"{disposition}:{'agree' if agrees_folded else 'disagree'}"] += 1
        if agrees_folded:
            continue
        if disposition == "scored":
            scored_causes[f"{framework}:{_bucket(row, record)}"] += 1
        pairs[f"manifest={manifest_verdict} file={file_verdict}"] += 1
        buckets[f"{framework}:{_bucket(row, record)}"] += 1

    return {
        "issue": "BEAAA-33661",
        "manifest_path": str(manifest_path),
        "manifest_job_id": manifest.get("job_id"),
        "manifest_self_declared": {
            "analysis_status": manifest.get("analysis_status"),
            "incomplete": manifest.get("incomplete"),
            "bind_eligibility": manifest.get("bind_eligibility"),
            "acceptance_gate": manifest.get("acceptance_gate"),
            "missing_required": (manifest.get("counts") or {}).get("missing_required"),
        },
        "rows_compared": sum(by_framework.values()),
        "unreadable_evidence_refs": unreadable,
        "misbound_rows": dict(sorted(misbound.items())),
        "casefold_only_disagreements": casefold_only,
        "by_framework": dict(sorted(by_framework.items())),
        "verdict_pairs": dict(sorted(pairs.items())),
        "cause_buckets": dict(sorted(buckets.items())),
        "by_ledger_disposition": dict(sorted(by_disposition.items())),
        "scored_row_disagreement_causes": dict(sorted(scored_causes.items())),
        "self_consistency": dict(sorted(self_consistency.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = census(args.evidence_root.resolve(), args.manifest, args.ledger)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
