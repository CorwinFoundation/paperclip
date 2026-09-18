"""BEAAA-33677 round 1 (cohort round 2 overall): select up to 200 of the 457 never-touched C0 rows.

Same selection rule as BEAAA-33673: largest-remainder proportional allocation
across the remainder families, ascending stable_case_id within family, no
reselection and no top-up. The only difference is the pool: the 607 rows of the
zero-build families less the 150 that BEAAA-33673 already consumed.
"""
from __future__ import annotations
import argparse, collections, hashlib, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import beaaa33673_zero_build_slice as Z
from tools import beaaa31092_content_slice as S

PRIOR_COHORTS = ["artifacts/beaaa-33673/cohort-150.json"]
COHORT_KEYS = ("family_id", "stable_case_id", "source_of_record", "object_locator",
               "reporting_status", "dedupe_sha256", "na_rule", "evidence_fields")


def allocate(pool_by_family: dict[str, list], size: int) -> dict[str, int]:
    total = sum(len(v) for v in pool_by_family.values())
    exact = {f: len(v) * size / total for f, v in pool_by_family.items()}
    take = {f: min(int(v), len(pool_by_family[f])) for f, v in exact.items()}
    remainder = sorted(exact, key=lambda f: (-(exact[f] - int(exact[f])), f))
    i = 0
    while sum(take.values()) < size and i < len(remainder) * 4:
        f = remainder[i % len(remainder)]
        if take[f] < len(pool_by_family[f]):
            take[f] += 1
        i += 1
    return take


def select(size: int, round_id: str, master_sha: str) -> dict:
    inv_path = ROOT / Z.PINNED_INVENTORY_PATH
    raw = inv_path.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != Z.INVENTORY_SHA256:
        raise ValueError(f"inventory pin mismatch: {got} != {Z.INVENTORY_SHA256}")
    inventory = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    zero_build = [r for r in inventory if r["family_id"] in Z.SLICE_FAMILIES]
    consumed = set()
    for rel in PRIOR_COHORTS:
        prior = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        consumed |= {r["stable_case_id"] for r in prior["rows"]}
    pool = [r for r in zero_build if r["stable_case_id"] not in consumed]
    by_family: dict[str, list] = collections.defaultdict(list)
    for r in sorted(pool, key=lambda r: r["stable_case_id"]):
        by_family[r["family_id"]].append(r)
    take = allocate(dict(by_family), min(size, len(pool)))
    chosen = [r for f in sorted(by_family) for r in by_family[f][:take[f]]]
    fp = collections.Counter(r["semantic_behavior_fingerprint"] for r in chosen)
    pool_fp = collections.Counter(r["semantic_behavior_fingerprint"] for r in pool)
    return {
        "issue": "BEAAA-33677",
        "capture_round": round_id,
        "cohort_size": len(chosen),
        "zero_build_family_rows": len(zero_build),
        "consumed_by_prior_slices": len(consumed),
        "pool_size": len(pool),
        "inventory_sha256": Z.INVENTORY_SHA256,
        "inventory_path": str(Path(Z.PINNED_INVENTORY_PATH).relative_to(ROOT)),
        "master_sha": master_sha,
        "selection_rule": ("largest-remainder proportional allocation across the remainder "
                           "families; within family ascending stable_case_id; no reselection, "
                           "no top-up"),
        "per_family_allocation": {f: take[f] for f in sorted(take) if take[f]},
        "intra_cohort_dedupe_collisions": sum(1 for v in fp.values() if v > 1),
        "pool_dedupe_collisions": sum(1 for v in pool_fp.values() if v > 1),
        "rows": [{k: r[k] for k in COHORT_KEYS if k in r} for r in chosen],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=200)
    ap.add_argument("--round", default="beaaa-33677-r1")
    ap.add_argument("--master-sha", required=True)
    ap.add_argument("--output", type=Path, required=True)
    a = ap.parse_args()
    cohort = select(a.size, a.round, a.master_sha)
    a.output.write_text(json.dumps(cohort, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in cohort.items() if k != "rows"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
