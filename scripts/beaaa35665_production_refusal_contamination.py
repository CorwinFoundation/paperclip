"""BEAAA-35665 deliverable 3: has the scope-refusal defect reached PRODUCTION scoring?

The CEO's question, exactly: of the 261 production-scored rows on this programme's
denominator, how many are scope refusals recorded as scored failures, and in which
families. Zero target calls, zero judge calls, no ledger write -- stored evidence only.

Method, and the two things that make the answer non-obvious:

1. A row can have SEVERAL stored responses, in different capture ledgers, from
   different targets. Picking one per row silently decides the answer: preferring the
   first entry that carries a verdict returns ZERO refusals here, while classifying
   every stored response returns 27. So classify all of them and attribute by source.

2. Having refusal-bearing evidence somewhere is not the same as being SCORED on it.
   A row's production score comes from the path named in its
   ``execution_evidence_status``, not from whatever ledger happens to mention its case
   id. The 33xxx captures are exploratory; nothing reads them.

Families where declining IS the expected behaviour are excluded from the contamination
count -- grading a refusal as a failure there is the oracle working.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.beaaa35665_scope_refusal_audit import is_scope_refusal  # noqa: E402

CATALOGUE = ROOT / "artifacts" / "beaaa-18720" / "unified-probe-inventory.jsonl"
REGISTRY_SRC = ROOT / "tools" / "active_test_registry.py"
EVIDENCE_KEYS = ("recorded_target_response", "target_response", "recorded_response", "raw_response", "response")
REFUSAL_IS_EXPECTED = frozenset({
    "p1_4_refusal_golden", "cbrn_refusal", "cyber_misuse_refusal",
    "harmful_output_golden", "out_of_scope_containment",
})


def _walk(obj, depth: int = 0):
    if depth > 6:
        return
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk(value, depth + 1)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk(value, depth + 1)


def _docs(path: Path) -> list:
    text = path.read_text(encoding="utf-8", errors="replace")
    if not any(key in text for key in EVIDENCE_KEYS):
        return []
    try:
        return [json.loads(text)]
    except json.JSONDecodeError:
        out = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return out


def audit(prod_registry: Path) -> dict:
    catalogue = [json.loads(l) for l in CATALOGUE.read_text(encoding="utf-8").splitlines() if l.strip()]
    family_of = {r["dedupe_sha256"]: r["family_id"] for r in catalogue}
    by_case = {r["stable_case_id"]: r["dedupe_sha256"] for r in catalogue}
    catalogue_digests = set(family_of)

    data = json.loads(prod_registry.read_text(encoding="utf-8"))
    registry = data if isinstance(data, list) else data["rows"]
    scored = {
        r["candidate_dedupe_sha256"]: r
        for r in registry
        if r.get("candidate_dedupe_sha256") in catalogue_digests and r.get("scoring_active")
    }
    case_ids = {str(r.get("case_id")): d for d, r in scored.items()}

    per_source: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    refusal_rows: set[str] = set()
    contaminated: dict[str, set[str]] = collections.defaultdict(set)
    with_text: set[str] = set()
    for path in sorted(list((ROOT / "artifacts").rglob("*.json")) + list((ROOT / "artifacts").rglob("*.jsonl"))):
        if path.stat().st_size > 80_000_000:
            continue
        for doc in _docs(path):
            for node in _walk(doc):
                response = next((node[k] for k in EVIDENCE_KEYS if isinstance(node.get(k), str) and node[k].strip()), None)
                if response is None:
                    continue
                case = str(node.get("stable_case_id") or node.get("case_id") or "")
                digest = str(node.get("dedupe_sha256") or "") or by_case.get(case, "") or case_ids.get(case, "")
                if digest not in scored:
                    continue
                with_text.add(digest)
                source = path.relative_to(ROOT / "artifacts").parts[0]
                verdict = str(node.get("verdict") or node.get("disposition") or "?")
                per_source[source]["n"] += 1
                if not is_scope_refusal(response):
                    continue
                per_source[source]["refusal"] += 1
                refusal_rows.add(digest)
                if verdict.lower() == "fail":
                    per_source[source]["refusal_graded_fail"] += 1
                    if family_of[digest] not in REFUSAL_IS_EXPECTED:
                        contaminated[digest].add(source)

    registry_src = REGISTRY_SRC.read_text(encoding="utf-8")
    return {
        "production_scored_catalogue_rows": len(scored),
        "rows_with_retrievable_response_text": len(with_text),
        "rows_with_no_retrievable_response_text": len(scored) - len(with_text),
        "rows_with_any_refusal_evidence": len(refusal_rows),
        "rows_with_refusal_graded_fail": len(contaminated),
        "contaminated_by_family": dict(collections.Counter(family_of[d] for d in contaminated).most_common()),
        "contaminated_evidence_sources": dict(collections.Counter(s for v in contaminated.values() for s in v).most_common()),
        "contaminated_source_read_by_registry": {
            s: s in registry_src for s in {s for v in contaminated.values() for s in v}
        },
        "contaminated_weights": dict(collections.Counter(
            str(scored[d].get("effective_underwriting_weight")) for d in contaminated).most_common()),
        "per_source": {s: dict(c) for s, c in sorted(per_source.items(), key=lambda kv: -kv[1]["n"])},
        "evidence_class_totals": dict(collections.Counter(
            r.get("execution_evidence_status") for r in scored.values()).most_common()),
        "weight_by_evidence_class": {
            cls: dict(collections.Counter(
                str(r.get("effective_underwriting_weight"))
                for r in scored.values() if r.get("execution_evidence_status") == cls).most_common())
            for cls in {r.get("execution_evidence_status") for r in scored.values()}
        },
        "missing_response_text_by_family": dict(collections.Counter(
            family_of[d] for d in scored if d not in with_text).most_common()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prod-registry", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts" / "beaaa-35665")
    args = parser.parse_args()
    report = audit(args.prod_registry)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "production_refusal_contamination.json").write_text(
        json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
