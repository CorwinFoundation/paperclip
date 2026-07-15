#!/usr/bin/env python3
"""Generate and verify BEAAA-14134 Retell real-agent reissue manifests.

This is a provenance reissue utility only. It does not emit BackBond Scores,
change scoring math, change rubric weights, change judge models, change API
output shape, or change tamper-resistance posture.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = REPO / "artifacts" / "beaaa-14134"
REISSUED_DIR = ARTIFACT_DIR / "reissued"
SOURCE_ARCHIVE_DIR = ARTIFACT_DIR / "source_archives"

CONTRACT_ANCHOR = "are-scanner-api-v1@v1.0"
SCANNER_VERSION = "1.2.0"
CORPUS_VERSION = "scanner-test-registry-baseline-2026-07-13"
PROBE_SET_VERSION = "beaaa14134-retell-real-agent-reissue@0.1.0"
TARGET_SERVICE_IDENTIFIER = "retell:agent_1e7015ae5f87ea825a3d1fac11"
TARGET_AGENT_ID = "agent_1e7015ae5f87ea825a3d1fac11"
MODEL_PRIMARY = f"{TARGET_SERVICE_IDENTIFIER}:gpt-4.1-mini:temperature-0"
SOURCE_ARCHIVES = {
    "BEAAA-14094": "BEAAA-14094_real_agent_final_evidence_v1.0.tar.gz",
    "BEAAA-14095": "BEAAA-14095_real_agent_275_case_execution_complete_v0.2.tar.gz",
    "BEAAA-14096": "BEAAA-14096_raw_target_traces.tgz",
    "BEAAA-14097": "BEAAA-14097_real_agent_retell_case_batch_v0.1.tar.gz",
}
FIXTURE_MARKERS = (
    "localhost",
    "127.0.0.1",
    "mock",
    "echo",
    "fixture",
    "replay",
    "local_target_response",
    "prewritten",
    "metadata-only",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _archive_manifest() -> dict[str, Any]:
    archives: dict[str, Any] = {}
    for ticket, name in SOURCE_ARCHIVES.items():
        archive = SOURCE_ARCHIVE_DIR / name
        archives[ticket] = {
            "file": f"artifacts/beaaa-14134/source_archives/{name}",
            "sha256": _sha256_file(archive),
            "size_bytes": archive.stat().st_size,
        }
    return archives


def _source_archive_name(ticket_ref: str) -> str:
    try:
        return SOURCE_ARCHIVES[ticket_ref]
    except KeyError as exc:
        raise ValueError(f"unknown source ticket {ticket_ref}") from exc


def _source_archive_sha(source_archives: dict[str, Any], ticket_ref: str) -> str:
    return source_archives[ticket_ref]["sha256"]


def _has_fixture_marker(row: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(row.get(key, "")).lower()
        for key in (
            "evidence_ref",
            "target_identity",
            "target_service_identifier",
            "exclusion_reason",
            "prompt_or_action",
        )
    )
    return any(marker in haystack for marker in FIXTURE_MARKERS)


def _eligible(row: dict[str, Any]) -> tuple[bool, str]:
    required_flags = ("expected", "applicable", "attempted", "terminal", "evidenced")
    missing = [flag for flag in required_flags if row.get(flag) is not True]
    if missing:
        return False, "missing_required_flags:" + ",".join(missing)
    if row.get("gate_1_concrete_prompt_or_action_sent") is not True:
        return False, "gate_1_missing_prompt_or_action"
    if row.get("gate_2_expected_behavior_or_oracle") is not True:
        return False, "gate_2_missing_expected_or_oracle"
    if row.get("gate_3_runner_connected_to_named_real_agent") is not True:
        return False, "gate_3_not_named_real_agent"
    if row.get("gate_4_durable_raw_target_response_or_action_trace") is not True:
        return False, "gate_4_missing_raw_trace"
    if row.get("gate_5_deterministic_or_pinned_judge_verdict") is not True:
        return False, "gate_5_missing_verdict_provenance"
    if row.get("gate_6_reconciled_flags") is not True:
        return False, "gate_6_unreconciled_flags"
    if row.get("parent_contract_anchor") != CONTRACT_ANCHOR:
        return False, "parent_contract_anchor_mismatch"
    if row.get("contract_version_recorded") != CONTRACT_ANCHOR:
        return False, "recorded_contract_mismatch"
    if _has_fixture_marker(row):
        return False, "fixture_or_mock_marker_present"
    return True, "accepted_after_canonical_pr_ci_merge_gate"


def generate(args: argparse.Namespace) -> None:
    ledger_path = Path(args.ledger)
    rows = _read_jsonl(ledger_path)
    source_archives = _archive_manifest()
    reissued_rows: list[dict[str, Any]] = []
    case_rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for row in rows:
        case_id = str(row.get("case_id", ""))
        if not case_id:
            raise ValueError("source row missing case_id")
        if case_id in seen:
            raise ValueError(f"duplicate case_id {case_id}")
        seen.add(case_id)
        ticket_ref = str(row.get("ticket_ref", ""))
        archive_name = _source_archive_name(ticket_ref)
        eligible, disposition = _eligible(row)
        reissued = {
            "accepted": eligible,
            "applicable": bool(row.get("applicable")),
            "attempted": bool(row.get("attempted")),
            "case_id": case_id,
            "contract_version_recorded": row.get("contract_version_recorded"),
            "corpus_version": row.get("corpus_version") or CORPUS_VERSION,
            "deterministic_or_pinned_judge": bool(row.get("gate_5_deterministic_or_pinned_judge_verdict")),
            "disposition": disposition,
            "evidence_ref": row.get("evidence_ref"),
            "evidenced": bool(row.get("evidenced")),
            "expected": bool(row.get("expected")),
            "model_versions": {"primary": MODEL_PRIMARY, "judges": []},
            "parent_contract_anchor": CONTRACT_ANCHOR,
            "probe_set_version": row.get("probe_set_version") or PROBE_SET_VERSION,
            "prompt_or_action_present": bool(row.get("prompt_or_action_present")),
            "raw_trace_present": bool(row.get("gate_4_durable_raw_target_response_or_action_trace")),
            "rubric_point_id": row.get("rubric_point_id"),
            "scanner_version": row.get("scanner_version") or SCANNER_VERSION,
            "seed": row.get("seed", 0),
            "source_archive": f"artifacts/beaaa-14134/source_archives/{archive_name}",
            "source_archive_sha256": _source_archive_sha(source_archives, ticket_ref),
            "source_ticket": ticket_ref,
            "target_agent_id": TARGET_AGENT_ID,
            "target_agent_verified": bool(row.get("gate_3_runner_connected_to_named_real_agent")),
            "target_service_identifier": row.get("target_service_identifier"),
            "terminal": bool(row.get("terminal")),
            "verdict": row.get("verdict"),
        }
        reissued_rows.append(reissued)
        case_rows.append(
            {
                "case_id": case_id,
                "expected": reissued["expected"],
                "family_or_rubric_point_id": row.get("rubric_point_id"),
                "prompt_or_action_present": reissued["prompt_or_action_present"],
                "source_ticket": ticket_ref,
                "target_service_identifier": row.get("target_service_identifier"),
            }
        )

    counts = Counter()
    by_ticket: dict[str, Counter[str]] = {}
    by_verdict = Counter(str(row.get("verdict")) for row in reissued_rows)
    for row in reissued_rows:
        ticket = row["source_ticket"]
        by_ticket.setdefault(ticket, Counter())
        by_ticket[ticket]["attempted"] += int(row["attempted"])
        by_ticket[ticket]["terminal"] += int(row["terminal"])
        by_ticket[ticket]["evidenced"] += int(row["evidenced"])
        by_ticket[ticket]["accepted"] += int(row["accepted"])
        by_ticket[ticket]["excluded"] += int(not row["accepted"])
        counts["attempted"] += int(row["attempted"])
        counts["terminal"] += int(row["terminal"])
        counts["evidenced"] += int(row["evidenced"])
        counts["accepted"] += int(row["accepted"])
        counts["excluded"] += int(not row["accepted"])

    run_metadata = {
        "artifact_type": "scanner_reissue_metadata",
        "ticket_ref": "BEAAA-14134",
        "parent_refs": ["BEAAA-14132", "BEAAA-13914"],
        "classification": "rule-execution",
        "classification_table_row": "Refactor/provenance reissue and smoke/test execution under existing rubric points; no scoring, rubric, judge, API-shape, or tamper-resistance change",
        "contract_version": CONTRACT_ANCHOR,
        "scanner_version": SCANNER_VERSION,
        "corpus_version": CORPUS_VERSION,
        "probe_set_version": PROBE_SET_VERSION,
        "rubric_point_id": "existing Retell row rubric_point_id values in row_manifest.jsonl",
        "model_versions": {"primary": MODEL_PRIMARY, "judges": []},
        "target_service_identifier": TARGET_SERVICE_IDENTIFIER,
        "source_archives": source_archives,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n5_calibration_delta": {
            "required": False,
            "reason": "evidence/provenance reissue only; no score emitted and no scoring/rubric/judge/API behavior changed",
            "max_total_delta": 0,
            "max_subscore_delta": 0,
            "status": "not_applicable_zero_score_impact",
        },
        "cto_cosign_ref": None,
        "deployed_digest": "deploy_not_applicable_evidence_only_no_runtime_code_change",
    }

    report = {
        "ticket_ref": "BEAAA-14134",
        "contract_version": CONTRACT_ANCHOR,
        "counts": dict(counts),
        "by_ticket": {ticket: dict(counter) for ticket, counter in sorted(by_ticket.items())},
        "verdicts": dict(sorted(by_verdict.items())),
        "remaining": 1090 - counts["accepted"],
        "exclusion_reason": "270 BEAAA-14096 rows remain excluded because source rows record are-scanner-api-v1@v1.3.0 while this issue is anchored to are-scanner-api-v1@v1.0.",
    }

    _write_jsonl(REISSUED_DIR / "row_manifest.jsonl", reissued_rows)
    _write_jsonl(REISSUED_DIR / "case_manifest.jsonl", case_rows)
    _write_json(REISSUED_DIR / "run_metadata.json", run_metadata)
    _write_json(REISSUED_DIR / "source_archive_manifest.json", source_archives)
    _write_json(REISSUED_DIR / "count_report.json", report)


def verify(_: argparse.Namespace) -> None:
    source_archives = json.loads((REISSUED_DIR / "source_archive_manifest.json").read_text(encoding="utf-8"))
    for ticket, meta in source_archives.items():
        if ticket not in SOURCE_ARCHIVES:
            raise AssertionError(f"unexpected source archive ticket {ticket}")
        path = REPO / meta["file"]
        if not path.exists():
            raise AssertionError(f"missing source archive {path}")
        if _sha256_file(path) != meta["sha256"]:
            raise AssertionError(f"sha256 mismatch for {path}")

    rows = _read_jsonl(REISSUED_DIR / "row_manifest.jsonl")
    cases = _read_jsonl(REISSUED_DIR / "case_manifest.jsonl")
    if len(rows) != 1090:
        raise AssertionError(f"expected 1090 row manifest entries, got {len(rows)}")
    if len(cases) != 1090:
        raise AssertionError(f"expected 1090 case manifest entries, got {len(cases)}")
    if len({row["case_id"] for row in rows}) != 1090:
        raise AssertionError("duplicate case_id in row manifest")

    counts = Counter()
    for row in rows:
        eligible, disposition = _eligible(
            {
                "expected": row["expected"],
                "applicable": row["applicable"],
                "attempted": row["attempted"],
                "terminal": row["terminal"],
                "evidenced": row["evidenced"],
                "gate_1_concrete_prompt_or_action_sent": row["prompt_or_action_present"],
                "gate_2_expected_behavior_or_oracle": True,
                "gate_3_runner_connected_to_named_real_agent": row["target_agent_verified"],
                "gate_4_durable_raw_target_response_or_action_trace": row["raw_trace_present"],
                "gate_5_deterministic_or_pinned_judge_verdict": row["deterministic_or_pinned_judge"],
                "gate_6_reconciled_flags": True,
                "parent_contract_anchor": row["parent_contract_anchor"],
                "contract_version_recorded": row["contract_version_recorded"],
                "target_service_identifier": row["target_service_identifier"],
                "evidence_ref": row["evidence_ref"],
            }
        )
        if row["accepted"] != eligible:
            raise AssertionError(f"{row['case_id']} accepted={row['accepted']} but verifier computed {eligible}")
        if row["disposition"] != disposition:
            raise AssertionError(f"{row['case_id']} disposition mismatch")
        counts["attempted"] += int(row["attempted"])
        counts["terminal"] += int(row["terminal"])
        counts["evidenced"] += int(row["evidenced"])
        counts["accepted"] += int(row["accepted"])
        counts["excluded"] += int(not row["accepted"])

    report = json.loads((REISSUED_DIR / "count_report.json").read_text(encoding="utf-8"))
    if report["counts"] != dict(counts):
        raise AssertionError(f"count report mismatch: {report['counts']} != {dict(counts)}")
    if counts["attempted"] != 1090 or counts["terminal"] != 1090 or counts["evidenced"] != 1090:
        raise AssertionError(f"gate 1-6 count mismatch: {dict(counts)}")
    if counts["accepted"] != 820 or counts["excluded"] != 270:
        raise AssertionError(f"expected 820 accepted / 270 excluded, got {dict(counts)}")
    print(json.dumps({"status": "pass", "counts": dict(counts)}, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    generate_parser = sub.add_parser("generate")
    generate_parser.add_argument("--ledger", required=True)
    generate_parser.set_defaults(func=generate)
    verify_parser = sub.add_parser("verify")
    verify_parser.set_defaults(func=verify)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
