"""Fail-closed reproducibility check for the committed BEAAA-31092 rank-2 ledger.

BEAAA-33964 TASK B. The committed ``artifacts/beaaa-31092/rank2-ledger.json`` was
last authored at ``f5004d43`` (BEAAA-33611, PR #582, 2026-09-18T00:08:12Z). Two
ratified resolver fixes have landed since - BEAAA-33656's digest keying and
BEAAA-33941's roster keying (#607) - and neither moved the artifact, because
``build_ledger`` refuses the pinned manifest at ``_assert_manifest_fit_to_bind``
and ``retier_ledger`` only reverses gate-authored holds. So the committed counts
are a snapshot of the code as of ``f5004d43``, not a reading of current code.

The failure mode this check exists to prevent is the quiet one: a reproducibility
check that treats "the rebuild could not run" as "nothing to compare, therefore
fine". That inverts the guarantee. Here an unrunnable rebuild is a finding with
the same standing as a mismatch - both report
``ledger not reproducible under the current manifest`` - because a ledger nobody
can rebuild is exactly a ledger nobody can vouch for.

This check reads. It never writes an artifact and never attempts to route around
the fit-to-bind guard.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.beaaa31092_content_slice import build_ledger  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "artifacts/beaaa-31092/rank2-ledger.json"
INVENTORY = ROOT / "artifacts/beaaa-18720/unified-probe-inventory.jsonl"
RUN_MANIFEST = ROOT / "artifacts/beaaa-31092/rank2-run-manifest.json"
CONTROLS = ROOT / "artifacts/beaaa-31092/readmission-controls.json"

# BEAAA-33992. ``recorded_target_response`` is never carried by the manifest: it
# is read from the evidence export the row names, via ``_resolve_response``.
# This check used to call ``build_ledger`` without a root, so every one of the
# 187 rows resolved "no evidence directory supplied", every row was held
# ``missing_recorded_target_response``, and the rebuild scored zero - reported as
# 197 field diffs against the committed ledger. That is this check mis-reading
# its own inputs, not drift, and it reddened identically before BEAAA-33970 and
# before any capture; the fit-to-bind guard simply raised first, so nobody saw
# the second failure behind it.
#
# A missing export is therefore reported as one explicit non-comparable finding.
# It still fails - an unvouchable ledger is the thing this check exists to catch
# - but it no longer claims a drift it cannot have measured.
EVIDENCE_ROOT_ENV = "BEAAA31092_EVIDENCE_ROOT"
NOT_COMPARABLE = (
    "ledger not comparable: no evidence export supplied, so no row can resolve "
    "recorded_target_response and every row holds. Pass --evidence-root "
    f"(or set {EVIDENCE_ROOT_ENV}) pointing at the job export the manifest names"
)

NOT_REPRODUCIBLE = "ledger not reproducible under the current manifest"

# BEAAA-34038. The e1332067 export is not reachable from CI, so the check above
# cannot run there and reports one honest non-comparable finding. That leaves a
# question unanswered that matters on its own: ignoring provenance, does the
# CURRENT resolver code still derive the committed dispositions, hold reasons and
# counts from the committed inputs?
#
# The committed ledger carries ``recorded_target_response`` verbatim for the rows
# that resolved one. Replaying those strings back through the rebuild answers the
# derivation question and nothing else. It is deliberately NOT a substitute for
# the export: the response term is then sourced from the artifact under test, so
# that one term is self-referential by construction and proves only that the
# derivation is stable, never that the text is what the target said. This mode is
# therefore diagnostic - it prints its result as ADVISORY and never changes the
# exit code, which stays 1 whenever no real export was supplied.
EMPTY_RESPONSE_HOLD = "evidence file records no target response"

SELF_SOURCED = (
    "ADVISORY (not a pass): responses replayed from the committed ledger itself, "
    "so the provenance of recorded_target_response is assumed, not checked")


def _replay_export(committed: dict[str, Any], job_id: str, dest: Path) -> int:
    """Write an export-shaped tree of the ledger's own recorded responses.

    Returns the number of rows written. Rows the committed ledger holds without a
    response are skipped, so they stay unresolvable in the rebuild exactly as they
    are in the artifact.
    """
    root = (dest / job_id).resolve()
    written = 0
    for row in committed.get("rows", []):
        ref = row.get("evidence_ref") or ""
        response = row.get("recorded_target_response")
        if not ref.startswith(f"evidence://{job_id}/"):
            continue
        # A row the committed ledger holds as "evidence file records no target
        # response" is a row whose export file existed and was empty. Replaying it
        # as absent would produce "evidence file missing from the exported job" and
        # read as drift, so the empty file is replayed as empty.
        if not response:
            if EMPTY_RESPONSE_HOLD not in (row.get("hold_reasons") or []):
                continue
            payload: dict[str, Any] = {"responses": []}
        else:
            payload = {"responses": [response]}
        relative = ref.removeprefix(f"evidence://{job_id}/")
        target = (root / relative).resolve()
        if root not in target.parents:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload), encoding="utf-8")
        written += 1
    return written


def derivation_diff_from_committed_responses(
    ledger_path: Path = LEDGER,
    inventory_path: Path = INVENTORY,
    manifest_path: Path = RUN_MANIFEST,
    controls_path: Path | None = CONTROLS,
) -> list[str]:
    """Advisory diff of every derived term, responses replayed from the ledger."""
    committed = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    job_id = manifest.get("job_id")
    if not job_id:
        return [f"  {NOT_REPRODUCIBLE}: the run manifest names no job_id to replay under"]
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        written = _replay_export(committed, job_id, dest)
        try:
            rebuilt = build_ledger(
                Path(inventory_path), Path(manifest_path),
                evidence_root=(dest / job_id),
                job_id=job_id,
                controls=Path(controls_path) if controls_path else None)
        except Exception as exc:  # noqa: BLE001 - same rule as the real path
            return [f"  {NOT_REPRODUCIBLE}: the rebuild path raised {type(exc).__name__}: {exc}"]
    diffs = _diff(committed, rebuilt)
    header = (f"  replayed {written} committed response(s) under job {job_id}; "
              f"{len(diffs) or 'no'} derived field(s) differ")
    return [header, *diffs]


# The stamp describes the snapshot, so it is deliberately not part of what the
# rebuild has to reproduce - build_ledger cannot know the commit it runs at.
STAMP_KEY = "provenance_stamp"


def _diff(committed: dict[str, Any], rebuilt: dict[str, Any]) -> list[str]:
    """Field-level differences, stamp excluded, row lists compared by identity."""
    findings: list[str] = []
    keys = (set(committed) | set(rebuilt)) - {STAMP_KEY, "rows"}
    for key in sorted(keys):
        if key not in committed:
            findings.append(f"  {key}: absent from the committed ledger, rebuilt emits it")
        elif key not in rebuilt:
            findings.append(f"  {key}: present in the committed ledger, rebuilt drops it")
        elif committed[key] != rebuilt[key]:
            findings.append(
                f"  {key}: committed {json.dumps(committed[key], sort_keys=True)[:160]}"
                f" != rebuilt {json.dumps(rebuilt[key], sort_keys=True)[:160]}")
    c_rows = {row.get("stable_case_id"): row for row in committed.get("rows", [])}
    r_rows = {row.get("stable_case_id"): row for row in rebuilt.get("rows", [])}
    for case_id in sorted(set(c_rows) | set(r_rows)):
        if case_id not in c_rows:
            findings.append(f"  rows[{case_id}]: rebuilt only")
        elif case_id not in r_rows:
            findings.append(f"  rows[{case_id}]: committed only")
        elif c_rows[case_id] != r_rows[case_id]:
            findings.append(
                f"  rows[{case_id}]: committed disposition {c_rows[case_id].get('disposition')!r} "
                f"hold_reasons {c_rows[case_id].get('hold_reasons')} != rebuilt "
                f"{r_rows[case_id].get('disposition')!r} {r_rows[case_id].get('hold_reasons')}")
    return findings


def check_ledger_reproducible(
    ledger_path: Path = LEDGER,
    inventory_path: Path = INVENTORY,
    manifest_path: Path = RUN_MANIFEST,
    controls_path: Path | None = CONTROLS,
    evidence_root: Path | None = None,
) -> list[str]:
    """Findings that make the committed ledger unvouchable. Empty means reproducible.

    An exception out of the rebuild path is a finding, not an excuse to pass.
    """
    committed = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
    findings: list[str] = []
    if STAMP_KEY not in committed:
        findings.append(
            f"committed ledger carries no {STAMP_KEY}: a consumer reading "
            f"scored={committed.get('scored')} held={committed.get('held')} cannot tell "
            "which commit produced them")
    if evidence_root is None:
        findings.append(NOT_COMPARABLE)
        return findings
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    try:
        rebuilt = build_ledger(
            Path(inventory_path), Path(manifest_path),
            evidence_root=Path(evidence_root),
            job_id=manifest.get("job_id"),
            controls=Path(controls_path) if controls_path else None)
    except Exception as exc:  # noqa: BLE001 - any failure to rebuild is the finding
        findings.append(
            f"{NOT_REPRODUCIBLE}: the rebuild path raised "
            f"{type(exc).__name__}: {exc}")
        return findings
    diffs = _diff(committed, rebuilt)
    if diffs:
        findings.append(f"{NOT_REPRODUCIBLE}: {len(diffs)} field(s) differ")
        findings.extend(diffs)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument("--manifest", type=Path, default=RUN_MANIFEST)
    parser.add_argument(
        "--evidence-root", type=Path, default=None,
        help=f"job export directory; falls back to ${EVIDENCE_ROOT_ENV}")
    args = parser.parse_args(argv)
    evidence_root = args.evidence_root
    if evidence_root is None and os.environ.get(EVIDENCE_ROOT_ENV):
        evidence_root = Path(os.environ[EVIDENCE_ROOT_ENV])
    findings = check_ledger_reproducible(
        args.ledger, args.inventory, args.manifest, evidence_root=evidence_root)
    if not findings:
        print("committed rank-2 ledger reproduces from the pinned inventory + manifest")
        return 0
    print(f"BEAAA-33964 drift check FAILED ({len(findings)} finding(s)):")
    for finding in findings:
        print(f"- {finding}" if not finding.startswith("  ") else finding)
    if evidence_root is None:
        print(f"- {SELF_SOURCED}")
        for line in derivation_diff_from_committed_responses(
                args.ledger, args.inventory, args.manifest):
            print(line)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
