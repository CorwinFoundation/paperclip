"""BEAAA-35665: do the production-scored rows' evidence pointers actually resolve?

The CEO's question: of the 261 production-scored rows on this programme's denominator,
how many carry at least one unresolvable evidence pointer, and of those, how many are
non-zero-weight -- actually contributing to a score today.

A pointer is unresolvable when the file it names is absent, or when the file is present
but the fragment (``#ordinal=N``, ``#stable_case_id=X``) does not resolve inside it. Both
matter: a row scored on ``file#ordinal=884`` where the file exists but has no ordinal 884
is no better evidenced than one naming a missing file.

Weight is reported as three separate classes rather than collapsed, because the honest
answer depends on the distinction:
  - ``0.0``   -- explicitly zero-weight (C10 dev-tier); cannot move a price.
  - ``None``  -- no weight on the registry row. NOT the same as zero: the golden/bench
                 families carry None and are scored live by the bench lane at scan time,
                 so a None row can still contribute to a score.
  - non-zero  -- contributes directly.

Stored evidence only: zero target calls, zero judge calls, no ledger write.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / "artifacts" / "beaaa-18720" / "unified-probe-inventory.jsonl"

POINTER_FIELDS = (
    "saved_evidence_ref", "saved_result_ref", "execution_evidence_ref",
    "c10_activation_manifest", "source_pack", "content_candidate_source_pack",
)
# A repo-relative path, optionally followed by a #fragment.
POINTER_RE = re.compile(r"^(?P<path>[\w./\-]+\.(?:json|jsonl|yaml|yml|py))(?:#(?P<fragment>.+))?$")


def _resolve_fragment(path: Path, fragment: str) -> bool:
    """Does ``#key=value`` actually appear in the named file?"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if "=" not in fragment:
        return fragment in text
    key, _, value = fragment.partition("=")
    key, value = key.strip(), value.strip()
    if key == "ordinal":
        # An ordinal is a numeric field, so look for the key/value pair rather than the
        # bare number, which would match any digit sequence in the file.
        return f'"ordinal": {value}' in text or f'"ordinal":{value}' in text
    return f'"{value}"' in text


def classify(pointer: str) -> tuple[str, str]:
    """Return (status, detail) for one pointer string."""
    match = POINTER_RE.match(pointer.strip())
    if not match:
        return "not_a_pointer", pointer
    rel = match.group("path")
    fragment = match.group("fragment")
    target = ROOT / rel
    if not target.exists():
        return "file_missing", rel
    if fragment and not _resolve_fragment(target, fragment):
        return "fragment_unresolved", pointer
    return "resolves", pointer


def audit(prod_registry: Path) -> dict:
    catalogue = [json.loads(l) for l in CATALOGUE.read_text(encoding="utf-8").splitlines() if l.strip()]
    family_of = {r["dedupe_sha256"]: r["family_id"] for r in catalogue}
    data = json.loads(prod_registry.read_text(encoding="utf-8"))
    registry = data if isinstance(data, list) else data["rows"]
    scored = [
        r for r in registry
        if r.get("candidate_dedupe_sha256") in family_of and r.get("scoring_active")
    ]

    def weight_class(row: dict) -> str:
        weight = row.get("effective_underwriting_weight")
        if weight is None:
            return "none_scored_live_by_bench_lane"
        return "zero" if float(weight) == 0.0 else "non_zero"

    broken: dict[str, dict] = {}
    status_totals: collections.Counter = collections.Counter()
    pointer_totals: collections.Counter = collections.Counter()
    for row in scored:
        digest = row["candidate_dedupe_sha256"]
        failures = []
        for field in POINTER_FIELDS:
            value = row.get(field)
            if not isinstance(value, str) or not value.strip():
                continue
            pointer_totals[field] += 1
            status, detail = classify(value)
            status_totals[status] += 1
            if status in ("file_missing", "fragment_unresolved"):
                failures.append({"field": field, "status": status, "detail": detail})
        if failures:
            broken[digest] = {
                "family": family_of[digest],
                "case_id": row.get("case_id"),
                "evidence_class": row.get("execution_evidence_status"),
                "weight_class": weight_class(row),
                "effective_underwriting_weight": row.get("effective_underwriting_weight"),
                "priced_score_weight": row.get("priced_score_weight"),
                "failures": failures,
            }

    by_weight = collections.Counter(v["weight_class"] for v in broken.values())
    return {
        "production_scored_catalogue_rows": len(scored),
        "rows_with_at_least_one_unresolvable_pointer": len(broken),
        "rows_by_weight_class": dict(by_weight.most_common()),
        "non_zero_weight_rows_with_unresolvable_pointer": by_weight.get("non_zero", 0),
        "rows_with_no_pointer_field_at_all": sum(
            1 for r in scored if not any(isinstance(r.get(f), str) and r[f].strip() for f in POINTER_FIELDS)
        ),
        "pointer_status_totals": dict(status_totals.most_common()),
        "pointer_field_totals": dict(pointer_totals.most_common()),
        "broken_by_family": dict(collections.Counter(v["family"] for v in broken.values()).most_common()),
        "broken_by_evidence_class": dict(collections.Counter(v["evidence_class"] for v in broken.values()).most_common()),
        "missing_files": dict(collections.Counter(
            f["detail"] for v in broken.values() for f in v["failures"] if f["status"] == "file_missing"
        ).most_common()),
        "sample_broken_rows": [
            {"case_id": v["case_id"], "family": v["family"], "weight_class": v["weight_class"],
             "failures": v["failures"]}
            for v in list(broken.values())[:4]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prod-registry", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts" / "beaaa-35665")
    args = parser.parse_args()
    report = audit(args.prod_registry)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "evidence_pointer_audit.json").write_text(
        json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
