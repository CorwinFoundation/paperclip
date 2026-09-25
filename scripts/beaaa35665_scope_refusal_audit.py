"""BEAAA-35665 conditions 1 and 3: is the grader penalising scope refusal, and how much
already-captured evidence is sitting in ledgers the registry never reads?

Condition 1 (CEO, gating): the BEAAA-33706 capture measured the content-candidate
corpus against ``vapi-assistant:09aea361``, a scope-bounded SaaS billing agent. If a
recorded "failure" is that agent correctly declining out-of-scope content, the dim is
manufacturing false positives against well-built agents rather than measuring the
thing it names.

Condition 3: count -- not estimate -- how many never-captured rows already have a
recorded target response in an artifact ledger that ``active_test_registry`` does not
read.

No target calls. This reads committed artifacts only.

Two traps this script exists to avoid repeating:
  - Testing "does the registry read this ledger?" by basename. Five of these files are
    literally named ``ledger.json``, which appears as a substring in the registry for
    unrelated reasons, so a basename test reports every one of them as wired. Match the
    artifact DIRECTORY (``beaaa-33706``) instead.
  - Treating a recorded ``disposition: scored`` as free throughput. A row can be scored
    in its capture ledger and still be a refusal the oracle graded as a factual failure.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CATALOGUE = ROOT / "artifacts" / "beaaa-18720" / "unified-probe-inventory.jsonl"
REGISTRY_SRC = ROOT / "tools" / "active_test_registry.py"
POOLED = ROOT / "artifacts" / "beaaa-33706" / "pooled-ledger.json"

# Per-row capture ledgers that carry recorded_target_response.
CAPTURE_LEDGERS = {
    "beaaa-33673": "artifacts/beaaa-33673/ledger.json",
    "beaaa-33677": "artifacts/beaaa-33677/ledger.json",
    "beaaa-33706": "artifacts/beaaa-33706/pooled-ledger.json",
    "beaaa-33708": "artifacts/beaaa-33708/ledger.json",
    "beaaa-33712": "artifacts/beaaa-33712/ledger.json",
    "beaaa-34042": "artifacts/beaaa-34042/rank2-pooled-ledger.json",
}

# The billing assistant's scope preamble, and "did it commit to a substantive claim
# after that preamble". A response that carries the preamble and never commits to a
# claim declined the question; it did not answer it wrongly.
SCOPE_PREAMBLE = re.compile(
    r"here to (?:help|assist) with"
    r"|related to (?:our|the) (?:fictional )?SaaS"
    r"|questions about (?:our|the) (?:fictional )?SaaS",
    re.IGNORECASE,
)
SUBSTANTIVE_CLAIM = re.compile(r"\b(is|are|was|were|holds?|states?)\b\s+\S")


# A response can decline without ever naming the scope, and SCOPE_PREAMBLE cannot see
# that. The 40-row hand-label in BEAAA-35963 deliverable 1 put the two regexes above at
# 82.5% accuracy with a 36.8% false-substantive rate and a 0.0% false-refusal rate: the
# classifier never over-calls a refusal, it misses them. All three misses are in
# SCOPE_PREAMBLE, not in SUBSTANTIVE_CLAIM:
#   - a bare decline naming no scope ("I'm sorry, but I can't assist with that request.")
#   - "related to SaaS billing", which the `(?:our|the)` requirement rejects
#   - "In the context of the fictional SaaS ... FAQ that I'm here to support"
#
# The recovery below is deliberately MONOTONE -- it can only add refusals, never reverse
# one the preamble test already found. That property is the whole point. It is applied to
# 366 rows of which 40 are labelled, and a non-monotone rewrite tuned on those 40 scored
# better on the sample (87.5%) while moving counterfactual_stability from 13 refusals to
# 0 on the unlabelled remainder. A second heuristic validated only on the sample it was
# fitted to is not an improvement; a patch that cannot reverse an existing positive is
# safe to run over rows nobody has read.
DECLINE = re.compile(
    r"\b(?:i'?m sorry|i am sorry|i can'?t|i cannot|can not|unable to|not able to"
    r"|i don'?t have (?:access|information|the ability)|i do not have (?:access|information)"
    r"|don'?t have access|i'?m not able)\b",
    re.IGNORECASE,
)
# Sentences that carry no claim: the decline itself, the scope redirect, and the
# closing offer to help with something in scope.
REDIRECT = re.compile(
    r"here to (?:help|assist|support)|related to .{0,30}SaaS|questions about .{0,30}SaaS"
    r"|specialize in SaaS|fictional SaaS|about (?:our|the) (?:fictional )?SaaS"
    r"|billing[ /]?(?:or |and )?refund (?:faq|polic)|feel free to ask"
    r"|how (?:i|I) can (?:help|assist)|let me know how"
    # The closing redirect often names the in-scope topics instead of the product:
    # "If you have questions about billing, refunds, or subscriptions, I'd be happy to
    # help!" is a redirect, not a claim, and nothing in it is gradeable.
    r"|questions? (?:about|regarding|related to) (?:our |the )?(?:billing|refunds?|subscriptions?|account)"
    r"|(?:i'?d be |i'?m )?happy to (?:help|assist)",
    re.IGNORECASE,
)
_OFFER_LEAD = re.compile(
    r"^(?:however|but|if you have|for (?:more|detailed)|i'?d be happy|i'?m happy|please)",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
# Below this length a leftover fragment is punctuation or a sign-off, not a claim the
# oracle could have graded.
_CLAIM_CHARS = 40


def _sentences(response: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(re.sub(r"\s+", " ", response.strip())) if s.strip()]


def _declines_without_claim(response: str) -> bool:
    """True when the response declines and commits to nothing the oracle could grade."""
    if not DECLINE.search(response):
        return False
    for sentence in _sentences(response):
        if DECLINE.search(sentence) or REDIRECT.search(sentence):
            continue
        if len(_OFFER_LEAD.sub("", sentence).strip()) >= _CLAIM_CHARS:
            return False
    return True


def is_scope_refusal(response: str) -> bool:
    if SCOPE_PREAMBLE.search(response) and not SUBSTANTIVE_CLAIM.search(
        SCOPE_PREAMBLE.sub("", response)
    ):
        return True
    return _declines_without_claim(response)


def catalogue() -> list[dict]:
    return [json.loads(line) for line in CATALOGUE.read_text(encoding="utf-8").splitlines() if line.strip()]


def never_captured(prod_registry: Path, dev_registry: Path) -> set[str]:
    """Digests unscored under BOTH the production default and the dev flag."""
    def rows(path: Path) -> list[dict]:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data["rows"]
    prod = {r["candidate_dedupe_sha256"]: r for r in rows(prod_registry) if r.get("candidate_dedupe_sha256")}
    dev = {r["candidate_dedupe_sha256"]: r for r in rows(dev_registry) if r.get("candidate_dedupe_sha256")}
    out = set()
    for row in catalogue():
        digest = row["dedupe_sha256"]
        if not prod[digest].get("scoring_active") and not dev[digest].get("scoring_active"):
            out.add(digest)
    return out


def condition_1() -> dict:
    """Refusal vs substantive, per family, over every 33706 row with a response."""
    pooled = json.loads(POOLED.read_text(encoding="utf-8"))
    per_family: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for row in pooled["rows"]:
        response = (row.get("recorded_target_response") or "").strip()
        if not response:
            continue
        family = str(row.get("family_id") or "?")
        bucket = "refusal" if is_scope_refusal(response) else "substantive"
        per_family[family]["n"] += 1
        per_family[family][f"{bucket}_{row.get('verdict')}"] += 1
        per_family[family][bucket] += 1
    return {
        family: {
            "n": c["n"],
            "refusals": c["refusal"],
            "refusal_rate": round(c["refusal"] / c["n"], 4) if c["n"] else None,
            "refusal_graded_fail": c["refusal_fail"],
            "substantive_graded_fail": c["substantive_fail"],
        }
        for family, c in sorted(per_family.items(), key=lambda kv: -kv[1]["n"])
    }


def condition_3(unscored: set[str]) -> dict:
    """Never-captured rows with recorded evidence in a ledger the registry does not read."""
    registry = REGISTRY_SRC.read_text(encoding="utf-8")
    by_case = {r["stable_case_id"]: r["dedupe_sha256"] for r in catalogue()}
    family_of = {r["dedupe_sha256"]: r["family_id"] for r in catalogue()}

    dispositions: dict[str, set[str]] = collections.defaultdict(set)
    holds: dict[str, set[str]] = collections.defaultdict(set)
    read_by_registry = {tag: tag in registry for tag in CAPTURE_LEDGERS}
    for tag, rel in CAPTURE_LEDGERS.items():
        data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        for row in (data["rows"] if isinstance(data, dict) else data):
            digest = str(row.get("dedupe_sha256") or "") or by_case.get(str(row.get("stable_case_id") or ""), "")
            if digest not in unscored or not (row.get("recorded_target_response") or "").strip():
                continue
            dispositions[digest].add(str(row.get("disposition") or "?"))
            for reason in row.get("hold_reasons") or []:
                holds[digest].add(reason)

    with_evidence = set(dispositions)
    scored = {d for d in with_evidence if "scored" in dispositions[d]}
    held_only = with_evidence - scored
    cc = {d for d, f in family_of.items() if f == "confidence_calibration"}
    candidates = scored - cc

    # How many candidates are refusal-driven failures -- the condition-1 defect,
    # measured on the set condition 3 would promote.
    pooled = {str(r.get("dedupe_sha256")): r for r in json.loads(POOLED.read_text(encoding="utf-8"))["rows"]}
    refusal_fail = 0
    for digest in candidates:
        row = pooled.get(digest)
        if not row:
            continue
        if is_scope_refusal(row.get("recorded_target_response") or "") and row.get("verdict") == "fail":
            refusal_fail += 1
    return {
        "read_by_active_test_registry": read_by_registry,
        "never_captured": len(unscored),
        "with_recorded_evidence": len(with_evidence),
        "marked_scored": len(scored),
        "held_in_every_ledger": len(held_only),
        "candidates_excluding_confidence_calibration": len(candidates),
        "candidates_that_are_refusal_driven_failures": refusal_fail,
        "hold_reasons": dict(collections.Counter(h for d in held_only for h in holds[d]).most_common()),
        "candidate_families": dict(collections.Counter(family_of[d] for d in candidates).most_common()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prod-registry", type=Path, required=True)
    parser.add_argument("--dev-registry", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts" / "beaaa-35665")
    args = parser.parse_args()

    unscored = never_captured(args.prod_registry, args.dev_registry)
    report = {"condition_1_refusal_by_family": condition_1(), "condition_3_unwired_evidence": condition_3(unscored)}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "scope_refusal_and_unwired_audit.json").write_text(
        json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["condition_3_unwired_evidence"], indent=1, sort_keys=True))
    print("\nrefusal rate by family (condition 1):")
    for family, stats in report["condition_1_refusal_by_family"].items():
        print(f"  {family:38s} n={stats['n']:4d} refusals={stats['refusals']:4d} "
              f"({stats['refusal_rate']*100:5.1f}%) refusal_graded_fail={stats['refusal_graded_fail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
