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


def live_rejudger(judge, registry_rows: Iterable[Mapping[str, Any]]):
    """``rejudge`` with the live judge standing in for the recorded verdict.

    Rows a deterministic gate decides keep that decision and cost nothing.
    Every other text case is graded again by ``judge`` on the recorded
    responses, through the executor's own prompt and verdict mapping: one
    judge call per row and no target traffic.
    """

    from tools.registered_case_executor import _judge_materialized_text_case
    from tools.text_case_materializer import is_text_compatible_test, materialize_text_case

    by_case = {str(row.get("case_id")): row for row in registry_rows}

    def _rejudge(row: Mapping[str, Any], capability_manifest: Mapping[str, Any] | None):
        verdict, reason, source = rejudge(row, capability_manifest)
        if source != "recorded":
            return verdict, reason, source
        test_id, case_id = str(row.get("test_id")), str(row.get("case_id"))
        responses = row.get("responses")
        registry_row = by_case.get(case_id)
        if registry_row is None or not is_text_compatible_test(test_id):
            return verdict, "live_judge_skipped:not_a_registry_text_case", source
        if not isinstance(responses, list) or not responses:
            return verdict, "live_judge_skipped:no_recorded_responses", source
        try:
            spec = materialize_text_case(test_id, case_id, registry_row)
        except (KeyError, LookupError, ValueError) as exc:
            # e.g. hallucination_legal materializes by family id, not probe id.
            return verdict, f"live_judge_skipped:materialize_{type(exc).__name__}:{exc}", source
        try:
            judged = _judge_materialized_text_case(
                judge,
                prompts=list(spec.prompts),
                responses=[str(item) for item in responses],
                expected=spec.expected,
                judge_criteria=spec.judge_criteria,
            )
        except Exception as exc:  # name the row, keep the other 29
            return verdict, f"live_judge_error:{type(exc).__name__}", source
        return str(judged["verdict"]), "live_judge", "live_judge"

    return _rejudge


def oracle_miss(row: Mapping[str, Any]) -> dict[str, Any]:
    """Why the deterministic scope-refusal oracle returned ``None`` for a row.

    The replay unit's status output is the only window onto the evidence tree
    from an agent seat, so a row the oracle skips has to carry its own cause:
    which branch of ``_deterministic_refusal_disposition`` declined it, the
    ``expected`` keys it saw, and the head of each recorded response.
    """

    from harness.bench.aiuc_path_b import (
        REFUSAL_RE,
        UNSAFE_RESPONSE_RE,
        normalise_quotes,
    )

    expected = row.get("expected")
    responses = row.get("responses")
    keys = sorted(str(key) for key in expected) if isinstance(expected, Mapping) else []
    texts = [normalise_quotes(str(item)) for item in responses] if isinstance(responses, list) else []
    if not isinstance(expected, Mapping) or not isinstance(responses, list):
        branch = "row_shape_unrecognised"
    elif not texts:
        branch = "no_responses"
    elif any(UNSAFE_RESPONSE_RE.search(text) for text in texts):
        branch = "unsafe_pattern_matched"
    elif not all(REFUSAL_RE.search(text) for text in texts):
        branch = "not_every_response_a_refusal"
    else:
        branch = "expected_shape_unrecognised"
    return {
        "branch": branch,
        "expected_keys": keys,
        "refusal_matches": sum(1 for text in texts if REFUSAL_RE.search(text)),
        "responses": len(texts),
        "response_heads": [text[:160] for text in texts[:3]],
    }


# --- the tree has to speak the oracle's vocabulary ------------------------


def _oracle_expectation_keys() -> frozenset[str]:
    """Every ``expected`` key ``_deterministic_refusal_disposition`` reads.

    Taken from the executor's own constants so this cannot drift away from the
    gate it is checking.
    """

    from tools.registered_case_executor import (
        _CAPABILITY_PRESUPPOSING_EXPECTATION_KEYS,
    )

    return frozenset({"expected_disposition"}) | _CAPABILITY_PRESUPPOSING_EXPECTATION_KEYS


def _recognised_expected_keys(row: Mapping[str, Any]) -> set[str]:
    expected = row.get("expected")
    if not isinstance(expected, Mapping):
        return set()
    return set(expected) & _oracle_expectation_keys()


def assert_oracle_inputs_recognised(sample: list[dict[str, Any]]) -> int:
    """Refuse a sample whose ``expected`` vocabulary the oracle cannot read.

    A tree written by a different producer still loads, still samples, and
    still yields an agreement number -- one where every row reports
    ``decided_by: "recorded"`` because the oracle was handed a shape it does
    not recognise and correctly returned ``None`` each time. That number looks
    measured and is not, and this replay is run once, behind a root grant that
    is spent when it runs. So the schema is asserted rather than assumed.

    Note this is deliberately *not* "no gate fired": a sample of genuinely
    substantive answers fires nothing and is a valid result. The failure being
    caught is a sample where no row even carries a key the gate reads.
    """

    if not sample:
        return 0
    recognised = sum(1 for row in sample if _recognised_expected_keys(row))
    if recognised:
        return recognised
    seen: set[str] = set()
    for row in sample:
        expected = row.get("expected")
        if isinstance(expected, Mapping):
            seen.update(str(key) for key in expected)
    raise RejudgeError(
        "no sampled row carries an `expected` key the deterministic oracle "
        f"reads ({', '.join(sorted(_oracle_expectation_keys()))}); the tree "
        f"uses {', '.join(sorted(seen)) or 'no expectation keys at all'}. "
        "Re-judging this sample would report every row as `recorded` and "
        "publish an agreement number that only looks measured."
    )


# --- agreement ------------------------------------------------------------


def _label_index(labels: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(label["case_id"]): label for label in labels}


def agreement(sample: list[dict[str, Any]], labels: list[dict[str, Any]],
              capability_manifest: Mapping[str, Any] | None,
              verdict_fn=None) -> dict[str, Any]:
    """Re-judged verdicts against the CTO's labels, with the gaps named."""

    index = _label_index(labels)
    matched: list[dict[str, Any]] = []
    unlabelled: list[dict[str, Any]] = []
    for row in sample:
        case_id = str(row.get("case_id"))
        verdict, reason, source = (verdict_fn or rejudge)(row, capability_manifest)
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
        if not record["matches_cto"] and source in ("recorded", "live_judge"):
            record["oracle_miss"] = oracle_miss(row)
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
    recognised = assert_oracle_inputs_recognised(sample)
    labels = json.loads(LABELS_PATH.read_text(encoding="utf-8"))["labels"]
    result = agreement(sample, labels, manifest)
    result["rows_with_recognised_expected"] = recognised
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
