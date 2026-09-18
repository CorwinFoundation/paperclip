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
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.beaaa31092_content_slice import build_ledger  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "artifacts/beaaa-31092/rank2-ledger.json"
INVENTORY = ROOT / "artifacts/beaaa-18720/unified-probe-inventory.jsonl"
RUN_MANIFEST = ROOT / "artifacts/beaaa-31092/rank2-run-manifest.json"
CONTROLS = ROOT / "artifacts/beaaa-31092/readmission-controls.json"

NOT_REPRODUCIBLE = "ledger not reproducible under the current manifest"

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
    try:
        rebuilt = build_ledger(
            Path(inventory_path), Path(manifest_path),
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
    args = parser.parse_args(argv)
    findings = check_ledger_reproducible(args.ledger, args.inventory, args.manifest)
    if not findings:
        print("committed rank-2 ledger reproduces from the pinned inventory + manifest")
        return 0
    print(f"BEAAA-33964 drift check FAILED ({len(findings)} finding(s)):")
    for finding in findings:
        print(f"- {finding}" if not finding.startswith("  ") else finding)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
