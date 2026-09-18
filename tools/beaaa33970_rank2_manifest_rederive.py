#!/usr/bin/env python3
"""Re-derive the BEAAA-31092 rank-2 run manifest against the BEAAA-33966 declarations.

The pinned manifest embeds its own capability state, and that state still reads
undeclared: twenty-six rows carry ``incomplete_reason:
unavailable_capability:<cap>=None``.  BEAAA-33966 recorded eleven declarations
in ``artifacts/beaaa-33966/capability-declarations.json``, every one of them
``false``, but a declaration written to a side artifact does not reach a sealed
manifest.  This re-derives the manifest so each of those rows reads its
capability as declared and routes through the executor's existing
``declared_not_applicable`` disposition.

It is a disposition, not a score.  The CEO's 2026-09-18 ruling on BEAAA-33966
sets the bar it clears: a ``false`` unlocks only a disposition, so the derived
BEAAA-33199 record is sufficient basis.  A ``true`` would need the raw capture,
and this tool refuses to act on one.

Offline: no target call, no judge call, no verdict on any row that did not
already carry one.  Summaries are recomputed with the executor's current
helpers rather than carried over from the source, because a summary inherited
across a row rewrite describes evidence that no longer exists.
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
    evidenced_rows,
    seal_manifest,
    unjustified_non_acquisition_rows,
)

DEFAULT_MANIFEST = REPO_ROOT / "artifacts/beaaa-31092/rank2-run-manifest.json"
DEFAULT_DECLARATIONS = REPO_ROOT / "artifacts/beaaa-33966/capability-declarations.json"
SOURCE_MANIFEST_SHA256 = "ab9fdb0304b614e0689536b5bb8bd4a933591c3a0543472f523a2097992607cb"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_declarations(path: Path) -> tuple[dict[str, bool], dict[str, dict]]:
    """Return the name->value map and the per-name provenance record.

    A ``true`` declaration is rejected rather than applied.  The 33199 record is
    a derivation, and the ruling admits it as basis for a disposition only; a
    ``true`` would move a row toward a claim about what the target does, and
    that needs the raw capture nobody has.
    """
    document = json.loads(path.read_text(encoding="utf-8"))
    values = {str(k): bool(v) for k, v in document["capability_manifest"].items()}
    asserted_true = sorted(name for name, value in values.items() if value)
    if asserted_true:
        raise ValueError(
            "declarations assert true for "
            f"{asserted_true}; a true declaration needs the raw capture and may "
            "not be derived from the BEAAA-33199 manifest (CEO ruling 2026-09-18)"
        )
    provenance = {str(entry["name"]): entry for entry in document["capabilities"]}
    return values, provenance


def rederive(
    manifest_path: Path,
    declarations_path: Path,
    destination: Path,
) -> dict[str, int | bool]:
    source = json.loads(manifest_path.read_text(encoding="utf-8"))
    declared, provenance = load_declarations(declarations_path)
    declarations_sha256 = _sha256(declarations_path)
    output = copy.deepcopy(source)

    changed = 0
    for row in output["evidence_rows"]:
        reason = str(row.get("incomplete_reason") or "")
        prefix, _, detail = reason.partition(":")
        capability, _, _embedded = detail.partition("=")
        if prefix != "unavailable_capability" or declared.get(capability) is not False:
            continue
        entry = provenance.get(capability, {})
        row.update(
            status="complete",
            verdict="not_applicable",
            target_calls=0,
            judge_calls=0,
            incomplete_reason=None,
            acquisition_basis="not-applicable",
            na_evidence={
                "profile_capability_manifest": declared,
                "missing_capability_name": capability,
                "declared_value": False,
                "capability_gate": f"manifest.{capability}=true",
                "declaration_source": str(
                    declarations_path.relative_to(REPO_ROOT)
                    if declarations_path.is_absolute()
                    else declarations_path
                ),
                "declaration_source_sha256": declarations_sha256,
                "basis_artifact": entry.get("basis_artifact"),
                "basis_artifact_sha256": entry.get("basis_artifact_sha256"),
                "derivation": entry.get("derivation"),
            },
        )
        changed += 1

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

    output["capability_manifest"] = declared
    output["capability_manifest_basis"] = {
        name: {
            "basis_artifact": entry.get("basis_artifact"),
            "basis_artifact_sha256": entry.get("basis_artifact_sha256"),
            "derivation": entry.get("derivation"),
        }
        for name, entry in sorted(provenance.items())
    }
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
    output["bind_eligibility"] = (
        "eligible" if not incomplete_reasons else "blocked_base_suite_incomplete"
    )
    output["rederivation"] = {
        "issue": "BEAAA-33970",
        "source_manifest_sha256": _sha256(manifest_path),
        "source_missing_required": source["counts"]["missing_required"],
        "declarations_source": "artifacts/beaaa-33966/capability-declarations.json",
        "declarations_sha256": declarations_sha256,
        "converted_declared_false_rows": changed,
        "residual_incomplete_rows": len(missing),
        # BEAAA-33228 rows: acquisition_basis missing, na_evidence null,
        # target_calls 0, and a verdict the scan never measured.  No capability
        # reason names them, so no declaration reaches them.  They are why the
        # fourth acceptance-gate term stays false after this re-derivation.
        "residual_unjustified_non_acquisition_rows": unjustified,
    }

    output = seal_manifest(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "changed": changed,
        "expected": expected,
        "attempted": attempted,
        "terminal": terminal,
        "evidenced": evidenced,
        "missing_required": len(missing),
        "unjustified_non_acquisition": unjustified,
        "acceptance_gate_passed": bool(output["acceptance_gate"]["passed"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--declarations", type=Path, default=DEFAULT_DECLARATIONS)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--require-source-sha256",
        default=SOURCE_MANIFEST_SHA256,
        help="refuse to re-derive anything but the pinned source bytes",
    )
    args = parser.parse_args()
    if args.require_source_sha256:
        actual = _sha256(args.manifest)
        if actual != args.require_source_sha256:
            print(
                f"source manifest sha256 {actual} != required {args.require_source_sha256}",
                file=sys.stderr,
            )
            return 2
    summary = rederive(args.manifest, args.declarations, args.out or args.manifest)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
