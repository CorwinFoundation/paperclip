#!/usr/bin/env python3
"""Build BEAAA-19518 from the frozen Vapi evidence trees; performs no network I/O."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/beaaa-19518"
SCANNER_VERSION = "0.5.0"
CORPUS_VERSION = "fa32e0e081ffbd2953ea3df8aa8d2d0faa0464a3076d17dad9b1758804488988"
PROBE_SET_VERSION = "1.4"


def load(path: str, git_ref: str | None = None):
    target = ROOT / path
    if target.exists():
        return json.loads(target.read_text())
    if git_ref:
        return json.loads(subprocess.check_output(["git", "show", f"{git_ref}:{path}"]))
    raise FileNotFoundError(target)


def pointer(path: str, row_id: str) -> str:
    return f"{path}#row/{row_id}"


def main() -> None:
    deterministic_path = "artifacts/beaaa-19389/vapi-saved-response-scoring-verification-v1.0.json"
    adjudication_path = "artifacts/beaaa-19422/final-adjudication-candidate-v1.json"
    constrained_path = "artifacts/beaaa-19391/vapi-502-constrained-terminal-ledger-v1.0.json"
    capability_path = "artifacts/beaaa-19288/capability-analysis.json"
    registry_path = "artifacts/beaaa-19218/vapi-2000-probe-authorization-freeze-v1.4.jsonl"

    deterministic = load(deterministic_path)
    adjudication = load(adjudication_path)
    constrained = load(constrained_path)
    capability = load(capability_path, "origin/master")

    registry = {}
    with (ROOT / registry_path).open() as fh:
        for line in fh:
            row = json.loads(line)
            registry[row["stable_case_id"]] = row
    family_for = lambda row_id: registry.get(row_id, {}).get("family_id", "unmapped")

    judged = []
    invalid_rows = []
    constrained_ids = {r["stable_case_id"] for r in constrained["records"]}
    for row in deterministic["rows"]:
        rid = row["row_id"]
        if row["verdict"] == "INVALID":
            invalid_rows.append(
                {
                    "row_id": rid,
                    "family_id": family_for(rid),
                    "verifier_family": row["verifier_family"],
                    "terminal_decision": "terminal_invalid",
                    "supportable_by_existing_capability_evidence": rid in constrained_ids,
                    "family_specific_evidence": [row["evidence_pointer"]]
                    + ([pointer(constrained_path, rid)] if rid in constrained_ids else []),
                    "missing_capability": "supported deterministic verifier for the frozen behavior family",
                    "attempted_remedy": (
                        "lossless assessment-local structured/role adapter plus immutable-verdict replay"
                        if rid in constrained_ids
                        else "saved-response immutable-verdict replay and response-text supplementation"
                    ),
                    "internal_owner": "CTO",
                    "next_fix": "Specify and gate a pinned family verifier; rerun only under separate authorization.",
                }
            )
        else:
            judged.append((rid, family_for(rid), row["verdict"]))

    for row in adjudication["results"]:
        label = row["consensus_label"].upper()
        judged.append((row["stable_case_id"], row["family_id"], label))

    unresolved = adjudication["unresolved_results"]
    uncovered_source = [r for r in adjudication["review_packet"] if not r["supportable"]]
    uncovered = [
        {
            "row_id": r["stable_case_id"],
            "family_id": r["family_id"],
            "terminal_status": "uncovered",
            "evidence_pointer": pointer(adjudication_path, r["stable_case_id"]),
            "missing_capability_or_evidence": r["uncovered_reason"],
            "attempted_remedy": "materializability review under the frozen adjudication packet",
            "internal_owner": "CTO",
            "next_fix": "Author a materializable prompt/action reference and route it through judge-policy approval.",
        }
        for r in uncovered_source
    ]

    attempted_by_family: dict[str, set[str]] = defaultdict(set)
    for row in deterministic["rows"]:
        attempted_by_family[family_for(row["row_id"])].add(row["row_id"])
    for row in adjudication["review_packet"]:
        attempted_by_family[row["family_id"]].add(row["stable_case_id"])
    judged_by_family: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for rid, family, verdict in judged:
        judged_by_family[family].append((rid, verdict))

    scorecard = []
    for family in sorted(attempted_by_family):
        values = judged_by_family.get(family, [])
        counts = Counter(v for _, v in values)
        if not values:
            status = "INVALID"
        elif counts["FAIL"]:
            status = "FAIL"
        elif counts["PARTIAL"]:
            status = "PARTIAL"
        else:
            status = "PASS"
        scorecard.append(
            {
                "family_id": family,
                "status": status,
                "attempted_count": len(attempted_by_family[family]),
                "full_judged_count": len(values),
                "result_counts": {k: counts[k] for k in ("PASS", "FAIL", "PARTIAL")},
                "attempted_row_ids": sorted(attempted_by_family[family]),
                "full_judged_row_ids": sorted(rid for rid, _ in values),
                "evidence_pointers": [deterministic_path, adjudication_path],
            }
        )

    ledger = {
        "schema": "backbond.vapi_closeout_terminal_ledger/v1.1",
        "metadata": {
            "artifact_type": "closeout_receipt",
            "owner": "scanner_engineer",
            "persona_audience": "internal",
            "canonical_terms_only": True,
            "ticket_refs": ["BEAAA-19518", "BEAAA-19388"],
            "contracts_referenced": [
                "are-scanner-api-v1.md@v1.3.0",
                "scanner-implementation-supervision-v1.md@v1.1",
            ],
            "scanner_version": SCANNER_VERSION,
            "corpus_version": CORPUS_VERSION,
            "probe-set-version": PROBE_SET_VERSION,
            "rubric_point_id": "none",
            "N=5 calibration delta": "0.0000 (derived evidence only; no scoring/harness change)",
            "cto_cosign_ref": None,
            "classification": "rule-execution",
        },
        "denominators": {
            "frozen_registry": 2000,
            "observed_vapi_target_attempted": 1192,
            "offline_or_adjudicated_evidence_reviewed": 1022,
            "valid_behavior_judged": {
                "total": 350,
                "PASS": 284,
                "FAIL": 56,
                "PARTIAL": 10,
                "pass_rate_display_only": "81.1%",
                "not_an_overall_vapi_backbond_score": True,
            },
            "synthetic_capability_evidence": len(capability["cases"]),
        },
        "baseline_delta": {
            "deterministic_valid_baseline": 255,
            "net_new_valid_adjudicated": 95,
            "final_valid_behavior_judged": 350,
            "equation": "255 + 95 = 350",
        },
        "family_scorecard": scorecard,
        "deterministic_invalid": {
            "count": len(invalid_rows),
            "verifier_family_counts": dict(sorted(Counter(r["verifier_family"] for r in invalid_rows).items())),
            "records": invalid_rows,
        },
        "uncovered_judge_rows": {"count": len(uncovered), "records": uncovered},
        "remaining_unscored_categories": [
            {
                "category": "deterministic_invalid",
                "count": len(invalid_rows),
                "row_ids": sorted(r["row_id"] for r in invalid_rows),
                "missing_capability_or_evidence": "pinned deterministic family verifier",
                "attempted_remedy": "immutable-verdict replay; 120 rows also received lossless adapter evidence",
                "owner": "CTO",
                "next_fix": "approve family verifier specifications before any rerun",
            },
            {
                "category": "adjudication_unresolved",
                "count": len(unresolved),
                "row_ids": sorted(r["stable_case_id"] for r in unresolved),
                "missing_capability_or_evidence": "consensus label supportable under the frozen rubric",
                "attempted_remedy": "two-reviewer adjudication plus independent adjudicator",
                "owner": "CTO and Underwriter",
                "next_fix": "retain fail-closed unless a versioned rubric rule resolves the abstention",
            },
            {
                "category": "judge_uncovered",
                "count": len(uncovered),
                "row_ids": sorted(r["row_id"] for r in uncovered),
                "missing_capability_or_evidence": "materializable prompt/action evidence",
                "attempted_remedy": "frozen-packet materializability review",
                "owner": "CTO",
                "next_fix": "author evidence-bearing rows and seek judge-policy approval",
            },
            {
                "category": "synthetic_capability_only",
                "count": len(capability["cases"]),
                "row_ids": sorted(r["stable_case_id"] for r in capability["cases"]),
                "missing_capability_or_evidence": "authorized Vapi target tool/action/state surface",
                "attempted_remedy": "isolated deterministic synthetic capability adapter; zero network requests",
                "owner": "Vapi relationship owner and CTO",
                "next_fix": "retain separate from target behavior unless separately authorized",
            },
        ],
        "rollback": "Discard v1.1 derived outputs; retain v1.0 and frozen evidence; make no score or band claim.",
    }

    assert len(registry) == 2000
    assert len(invalid_rows) == 642
    assert len(uncovered) == 170
    assert len(unresolved) == 30
    assert len(capability["cases"]) == 596
    assert Counter(v for _, _, v in judged) == Counter(PASS=284, FAIL=56, PARTIAL=10)
    all_judged_ids = [rid for rid, _, _ in judged]
    assert len(all_judged_ids) == len(set(all_judged_ids)) == 350

    OUT.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(ledger, indent=2, sort_keys=True) + "\n"
    (OUT / "vapi-closeout-terminal-ledger-v1.1.json").write_text(encoded)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    closeout = f"""---
artifact_type: closeout
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This closeout gives internal decision-makers exact Vapi evidence denominators without converting partial coverage into an unsupported BackBond Score."
contracts_referenced: ["are-scanner-api-v1.md@v1.3.0", "scanner-implementation-supervision-v1.md@v1.1"]
ticket_refs: ["BEAAA-19518", "BEAAA-19388"]
proof_points_cited: []
version: 1.1
prior_version: "artifacts/beaaa-19388/BEAAA-19388-vapi-2000-coverage-closeout-v1.0.md"
self_audit_passed: true
escalations_noted: []
---

# Vapi closeout v1.1

## Decision

BEAAA-19388 v1.0 is superseded as the current accounting view, but retained as history. The v1.1 JSON ledger reconciles 2,000 frozen rows and preserves four separate denominators. It makes no overall Vapi BackBond Score or band claim.

## Exact behavior denominator

The deterministic baseline contains 255 valid judged cases. Final adjudication adds exactly 95 net-new valid cases: `255 + 95 = 350`. The 350 valid behavior-judged cases are 284 PASS, 56 FAIL, and 10 PARTIAL. The displayed `81.1%` is `284 / 350` only.

## Terminal accounting

- 642 deterministic INVALID rows reconcile exactly under `unsupported_family`; each row records evidence, missing verifier capability, attempted remedy, owner, and next fix.
- 170 uncovered judge rows have row-level terminal records and evidence pointers.
- 30 adjudication rows remain unresolved and fail closed.
- 596 synthetic capability cases remain a separate, non-Vapi-target denominator.

The machine-readable family scorecard records attempted and fully judged denominators, row IDs, status, result counts, and evidence pointers for each family.

## Evidence

- Ledger: `artifacts/beaaa-19518/vapi-closeout-terminal-ledger-v1.1.json`
- Ledger SHA-256: `{digest}`
- Scanner version: `{SCANNER_VERSION}`
- Corpus version: `{CORPUS_VERSION}`
- Probe-set version: `{PROBE_SET_VERSION}`
- Rubric points affected: none
- N=5 calibration delta: `0.0000` because this is a derived-evidence build with no scoring or harness change
- Contract: `contracts/are-scanner-api-v1.md@v1.3.0`

## Rollback

Discard the v1.1 derived outputs and retain v1.0 plus the frozen source evidence. Make no score or band claim.
"""
    (OUT / "BEAAA-19518-vapi-closeout-v1.1.md").write_text(closeout)


if __name__ == "__main__":
    main()
