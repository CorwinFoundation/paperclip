#!/usr/bin/env python3
"""Surface the recorded observational rejection on the 24 rows the gate flags.

BEAAA-33992. The card framed twenty-four rows as asserting "a ``fail`` the scan
never measured" and offered three dispositions, all premised on there being
nothing recorded for them. The evidence export says otherwise. All twenty-four
carry, in the file the manifest row itself names::

    "observational_rejection": "observational_fail_closed:ObservationalContractError"
    "judge": {"type": "structured_invariant", "llm_judge_calls": 0}
    "payloads": [], "responses": [], "verdict": "fail"

So the scan did record something: the observational contract could not be
satisfied before any dispatch, and the row failed closed. The ``fail`` is that
fail-closed verdict, a deliberate decision of the contract, not an unmeasured
assertion.

``api/full_scan_executor.py:1298`` already states what such a row should carry::

    elif result.observational_rejection is not None:
        na_evidence = {"observational_rejection": result.observational_rejection}

with the comment "Surface the rejection as na_evidence so the row is
distinguishable from an unjustified missing-acquisition row". That branch landed
in ``137941be`` (BEAAA-33228, PR #542) at 2026-09-17T01:37+03:00. The scan that
produced this manifest exported its evidence at 2026-09-16T15:14Z, roughly eight
hours earlier, so no row in the manifest could have been written through it -
and none was: zero of 187 carry an observational-rejection ``na_evidence``.

This applies that branch offline to a manifest built before it existed. It is
the same operation BEAAA-33970 was authorised to perform for the eleven
capability declarations, against the same manifest, one contract later.

It authors nothing. The ``na_evidence`` value is copied verbatim out of the
evidence file; a row whose evidence file records no ``observational_rejection``
is refused rather than justified. Verdict, ``target_calls`` and
``acquisition_basis`` are asserted unchanged on every row it touches: this
records why the row was never acquired, it does not claim an acquisition.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.full_scan_executor import (  # noqa: E402
    RECONCILED_TERMINAL_PROBE_STATUSES,
    build_untested_coverage,
    evidenced_rows,
    seal_manifest,
    unjustified_non_acquisition_rows,
)

DEFAULT_MANIFEST = REPO_ROOT / "artifacts/beaaa-31092/rank2-run-manifest.json"
# The manifest as BEAAA-33970 left it at PR #611 head 0f5a2473.
SOURCE_MANIFEST_SHA256 = "f1f32147343717e506ae15ca351d8d0c0f02c1f3e8dbe9469d50d45ab42a59d7"
CONTRACT_SITE = "api/full_scan_executor.py:1298 (BEAAA-33228, 137941be, PR #542)"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidence_path(evidence_root: Path, evidence_ref: str) -> Path:
    """Resolve a manifest ``evidence_ref`` the way ``_resolve_response`` does.

    Anchored on the ref, and the export directory must name the ref's job:
    BEAAA-33741's finding was that a root from another job otherwise binds by
    relative path alone.
    """
    if not evidence_ref or not evidence_ref.startswith("evidence://"):
        raise ValueError(f"row names no evidence ref: {evidence_ref!r}")
    ref_job, _, relative = evidence_ref.removeprefix("evidence://").partition("/")
    if not relative:
        raise ValueError(f"evidence ref names no path: {evidence_ref!r}")
    root = evidence_root.resolve()
    if root.name != ref_job:
        raise ValueError(f"export {root.name!r} is not job {ref_job!r}")
    return root / relative


def rederive(
    manifest_path: Path,
    evidence_root: Path,
    destination: Path,
) -> dict[str, object]:
    source = json.loads(manifest_path.read_text(encoding="utf-8"))
    output = copy.deepcopy(source)

    # The set this tool may touch is the executor's own predicate, not a list
    # written here. Anything it does not flag is out of scope by construction.
    flagged = {
        id(row): row for row in unjustified_non_acquisition_rows(output["evidence_rows"])
    }

    applied: list[str] = []
    refused: list[dict[str, str]] = []
    for row in output["evidence_rows"]:
        if id(row) not in flagged:
            continue
        before = (
            row.get("verdict"),
            int(row.get("target_calls") or 0),
            str(row.get("acquisition_basis") or ""),
        )
        path = _evidence_path(evidence_root, str(row.get("evidence_ref") or ""))
        if not path.is_file():
            refused.append({"probe_id": row["probe_id"], "reason": "evidence file absent"})
            continue
        evidence = json.loads(path.read_text(encoding="utf-8"))
        rejection = evidence.get("observational_rejection")
        if not rejection:
            # Fail closed. A row with nothing recorded is exactly the row the
            # card describes, and it stays flagged rather than being justified.
            refused.append(
                {"probe_id": row["probe_id"], "reason": "evidence records no observational_rejection"}
            )
            continue
        if evidence.get("verdict") != row.get("verdict"):
            raise ValueError(
                f"{row['probe_id']}: evidence verdict {evidence.get('verdict')!r} "
                f"!= manifest verdict {row.get('verdict')!r}"
            )
        if evidence.get("responses") or evidence.get("payloads"):
            raise ValueError(
                f"{row['probe_id']}: evidence records a dispatch; an observational "
                "rejection must precede one"
            )
        row["na_evidence"] = {
            "observational_rejection": str(rejection),
            "evidence_ref": row.get("evidence_ref"),
            "evidence_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "oracle_type": (evidence.get("judge") or {}).get("type"),
            "recorded_by": "scan",
            "surfaced_by": "BEAAA-33992",
            "contract_site": CONTRACT_SITE,
        }
        after = (
            row.get("verdict"),
            int(row.get("target_calls") or 0),
            str(row.get("acquisition_basis") or ""),
        )
        if before != after:
            raise ValueError(f"{row['probe_id']}: row result moved {before} -> {after}")
        applied.append(str(row["probe_id"]))

    rows = output["evidence_rows"]
    statuses = Counter(str(row.get("status") or "unknown") for row in rows)
    verdicts = Counter(str(row.get("verdict") or "unresolved") for row in rows)
    missing = [row["probe_id"] for row in rows if row.get("status") != "complete"]
    incomplete_reasons = [
        f"{row['probe_id']}:{row['incomplete_reason']}"
        for row in rows
        if row.get("status") != "complete" and row.get("incomplete_reason")
    ]

    attempted = len(rows)
    terminal = sum(
        count
        for status, count in statuses.items()
        if status in RECONCILED_TERMINAL_PROBE_STATUSES
    )
    evidenced = len(evidenced_rows(rows))
    unjustified = len(unjustified_non_acquisition_rows(rows))
    declared_expected = output.get("counts", {}).get("expected")
    expected_declared = declared_expected is not None
    expected = int(declared_expected) if expected_declared else attempted

    output["counts"]["missing_required"] = len(missing)
    output["counts"]["evidenced"] = evidenced
    output["counts"]["unjustified_non_acquisition"] = unjustified
    output["status_counts"] = dict(sorted(statuses.items()))
    output["verdict_counts"] = dict(sorted(verdicts.items()))
    output["incomplete_reasons"] = incomplete_reasons
    output["incomplete"] = bool(incomplete_reasons)
    output["analysis_status"] = "complete" if not incomplete_reasons else "analysis_incomplete"
    output["reconciliation"] = {
        "attempted": attempted,
        "terminal": terminal,
        "evidenced": evidenced,
        "unjustified_non_acquisition": unjustified,
        "by_status": dict(sorted(statuses.items())),
    }
    four_way_equal = expected_declared and expected == attempted == terminal == evidenced
    output["acceptance_gate"] = {
        "expected_declared": expected_declared,
        "canonical_reconciliation_equal": (
            expected_declared and expected == attempted == terminal
        ),
        "expected_attempted_terminal_evidenced_equal": four_way_equal,
        "incomplete_false": not incomplete_reasons,
        "zero_unapproved_missing_required": not missing,
        "passed": four_way_equal and not incomplete_reasons and not missing,
    }
    # BEAAA-33970 closeout: the embedded coverage statement was stale even before
    # this change - it read {"count": 0, "rows": []} while the live recompute over
    # the same rows returned 24, so a reader trusting the field concluded every row
    # was evidenced. Re-derived here rather than left alone, so the manifest's
    # coverage claim and its rows cannot disagree. Under this ruling it lands at
    # zero honestly: all 24 carry their recorded rejection.
    output["untested_coverage"] = build_untested_coverage(rows)
    output["bind_eligibility"] = (
        "eligible" if not incomplete_reasons else "blocked_base_suite_incomplete"
    )
    output["observational_rejection_surfacing"] = {
        "issue": "BEAAA-33992",
        "contract_site": CONTRACT_SITE,
        "source_manifest_sha256": _sha256(manifest_path),
        "evidence_export": str(evidence_root),
        "rows_flagged": len(flagged),
        "rows_surfaced": len(applied),
        "rows_refused": refused,
        "authored_justifications": 0,
        "target_calls_added": 0,
        "verdicts_changed": 0,
    }

    output = seal_manifest(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "flagged": len(flagged),
        "surfaced": len(applied),
        "refused": len(refused),
        "expected": expected,
        "attempted": attempted,
        "terminal": terminal,
        "evidenced": evidenced,
        "missing_required": len(missing),
        "unjustified_non_acquisition": unjustified,
        "acceptance_gate_passed": bool(output["acceptance_gate"]["passed"]),
        "manifest_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--require-source-sha256", default=SOURCE_MANIFEST_SHA256)
    args = parser.parse_args()
    if args.require_source_sha256:
        actual = _sha256(args.manifest)
        if actual != args.require_source_sha256:
            print(f"source manifest sha256 {actual} != required", file=sys.stderr)
            return 2
    summary = rederive(args.manifest, args.evidence_root, args.out or args.manifest)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
