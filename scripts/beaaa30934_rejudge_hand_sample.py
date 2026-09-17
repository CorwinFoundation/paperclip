#!/usr/bin/env python3
"""Re-judge the CTO's 30-row hand sample offline, against today's oracle.

BEAAA-30934 acceptance criterion 2: "The 30 sampled rows re-judged with the
CTO's hand labels as the reference; report agreement. Baseline was 6/30."

This spends **no target call and no judge call**. Both gates that decide these
rows today are deterministic functions of data already on disk:

* ``_deterministic_refusal_disposition`` (PR #553) reads the recorded responses
  and the case's ``expected`` contract. Its evidence records
  ``judge_called: False``.
* ``_resolve_applicability`` (PR #554/#559/#560) reads the family's declared
  gate and the job's capability manifest, and decides before dispatch.

So the re-judge is a pure replay over the 2026-09-08 evidence tree. It needs no
credentials, no network, and no scan — only read access to the tree, which on
AriClaw is what Founder card ``7afb9e88`` restores.

Usage:
    python scripts/beaaa30934_rejudge_hand_sample.py \\
        --evidence-root /var/lib/backbond/scans/2026-09-08/vapi/evidence \\
        [--capability-manifest artifacts/BEAAA-33199/capability-manifest.json]

Sampling is re-derived rather than trusted, because the published sample does
not name every row it counts (see ``artifacts/BEAAA-30934/cto-hand-sample-30.json``
-> ``unreconstructible``). The CTO's stated method is deterministic:

    ten largest failing families by fail count, ties broken alphabetically;
    within each family the fail rows sorted by case_id, sampled at indices
    0, n//2, n-1.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

LABELS_PATH = REPO_ROOT / "artifacts" / "BEAAA-30934" / "cto-hand-sample-30.json"
SAMPLE_FAMILIES = 10
SAMPLE_INDICES = (0, None, -1)  # None means n // 2


class RejudgeError(RuntimeError):
    """The replay refused to continue rather than report a guessed number."""


# --- reading the evidence tree -------------------------------------------


def load_rows(evidence_root: Path) -> list[dict[str, Any]]:
    """Every per-case evidence document under ``<root>/<job>/<family>/<case>.json``.

    ``EvidenceStore.write`` is the only writer of this layout, so the shape is
    the executor's own record, not a reconstruction.
    """

    try:
        readable = evidence_root.is_dir()
    except OSError as exc:
        # The expected failure on AriClaw today: /var/lib/backbond is
        # drwxr-x--- backbond:backbond and the scan account cannot traverse it,
        # so even stat() on a path inside the store raises. Name the card that
        # fixes it rather than leaking a bare PermissionError traceback.
        raise RejudgeError(
            f"evidence tree not reachable: {evidence_root} ({exc.strerror}). "
            "On AriClaw this is the traversal permission on /var/lib/backbond "
            "that Founder card 7afb9e88 (ops/scan-schedule/install.sh) restores."
        ) from exc
    if not readable:
        raise RejudgeError(f"evidence tree not readable: {evidence_root}")
    rows: list[dict[str, Any]] = []
    for path in sorted(evidence_root.glob("*/*/*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        record.setdefault("test_id", path.parent.name)
        record.setdefault("case_id", path.stem)
        rows.append(record)
    if not rows:
        raise RejudgeError(f"no evidence documents under {evidence_root}")
    return rows


def _verdict(row: Mapping[str, Any]) -> str:
    return str(row.get("verdict") or "").lower()


# --- re-deriving the sample the CTO described ----------------------------


def derive_sample(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The 30 rows the CTO's stated method selects, recomputed from the tree."""

    fails = [row for row in rows if _verdict(row) == "fail"]
    counts = Counter(str(row.get("test_id")) for row in fails)
    # Largest by fail count; ties broken alphabetically, which is the rule that
    # excluded resource_budget_enforcement from a five-way tie at 24.
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    families = [name for name, _ in ranked[:SAMPLE_FAMILIES]]

    sample: list[dict[str, Any]] = []
    for family in families:
        group = sorted(
            (row for row in fails if str(row.get("test_id")) == family),
            key=lambda row: str(row.get("case_id")),
        )
        n = len(group)
        for index in SAMPLE_INDICES:
            position = n // 2 if index is None else (index if index >= 0 else n + index)
            if 0 <= position < n:
                sample.append(group[position])
    # Same row can be selected twice in a family of one or two.
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for row in sample:
        key = (str(row.get("test_id")), str(row.get("case_id")))
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


# --- re-judging one row with today's deterministic gates -----------------


def rejudge(row: Mapping[str, Any], capability_manifest: Mapping[str, Any] | None):
    """Today's verdict for one recorded row. No target call, no judge call."""

    from tools.registered_case_executor import (
        _deterministic_refusal_disposition,
        _resolve_applicability,
    )

    test_id = str(row.get("test_id"))
    resolution = _resolve_applicability(
        test_id, {"capability_manifest": capability_manifest}
    )
    if not resolution.applicable:
        return "not_applicable", resolution.reason, "applicability_gate"

    expected = row.get("expected")
    responses = row.get("responses")
    if isinstance(expected, Mapping) and isinstance(responses, list):
        disposition = _deterministic_refusal_disposition(
            expected, [str(item) for item in responses]
        )
        if disposition is not None:
            verdict, reason = disposition
            return verdict, reason, "deterministic_scope_refusal_oracle"

    # Neither deterministic gate fires, so the row keeps the verdict the judge
    # gave it. Saying so is the honest answer: this replay does not re-run a
    # judge and must not pretend the verdict was revisited.
    return _verdict(row), "judge_verdict_unchanged_by_deterministic_gates", "recorded"


# --- agreement ------------------------------------------------------------


def _label_index(labels: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(label["case_id"]): label for label in labels}


def agreement(sample: list[dict[str, Any]], labels: list[dict[str, Any]],
              capability_manifest: Mapping[str, Any] | None) -> dict[str, Any]:
    """Re-judged verdicts against the CTO's labels, with the gaps named."""

    index = _label_index(labels)
    matched: list[dict[str, Any]] = []
    unlabelled: list[dict[str, Any]] = []
    for row in sample:
        case_id = str(row.get("case_id"))
        verdict, reason, source = rejudge(row, capability_manifest)
        record = {
            "family": str(row.get("test_id")),
            "case_id": case_id,
            "was": _verdict(row),
            "now": verdict,
            "reason": reason,
            "decided_by": source,
        }
        label = index.get(case_id)
        if label is None:
            unlabelled.append(record)
            continue
        # The CTO agreed with the judge iff the row is a real fail. The re-judge
        # matches that label iff it also still says fail.
        record["cto"] = label["cto"]
        record["cause"] = label["cause"]
        record["matches_cto"] = (verdict == "fail") == bool(label["agrees_with_judge"])
        matched.append(record)

    return {
        "sampled": len(sample),
        "labelled_by_case_id": len(matched),
        "unlabelled_in_published_sample": len(unlabelled),
        "matches_cto": sum(1 for r in matched if r["matches_cto"]),
        "baseline_judge_agreement": "6/30",
        "rows": matched,
        "unlabelled_rows": unlabelled,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument(
        "--capability-manifest",
        type=Path,
        default=REPO_ROOT / "artifacts" / "BEAAA-33199" / "capability-manifest.json",
    )
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    from harness.vapi_capability_manifest import declare_for_assistant, load_snapshot

    snapshot = load_snapshot(args.capability_manifest)
    manifest = declare_for_assistant(
        str(snapshot.get("assistant_id") or ""), manifest_path=args.capability_manifest
    )

    rows = load_rows(args.evidence_root)
    sample = derive_sample(rows)
    labels = json.loads(LABELS_PATH.read_text(encoding="utf-8"))["labels"]
    result = agreement(sample, labels, manifest)
    result["evidence_root"] = str(args.evidence_root)
    result["capability_manifest"] = manifest
    result["total_rows_read"] = len(rows)

    encoded = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.out:
        args.out.write_text(encoded, encoding="utf-8")
        print(args.out)
    else:
        print(encoded)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
