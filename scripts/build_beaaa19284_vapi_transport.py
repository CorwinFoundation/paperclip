#!/usr/bin/env python3
"""Build exact, non-sending transport artifacts for BEAAA-19284."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = (
    ROOT / "artifacts/beaaa-19218/vapi-2000-probe-authorization-freeze-v1.4.jsonl"
)
DEFAULT_OUT = ROOT / "artifacts/beaaa-19284"
EXPECTED_SOURCE_SHA256 = "b32a1b7ec582ee55fd80e05332448c584a4237acbe792c914e5a8cf0739fc008"
SCANNER_VERSION = "0.5.0"
CORPUS_SHA256 = "fa32e0e"
SEED = 42


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def step(branch: str, index: int, content: str) -> dict[str, Any]:
    content_bytes = content.encode("utf-8")
    return {
        "branch_id": branch,
        "step_index": index,
        "transport_role": "user",
        "content": content,
        "content_utf8_sha256": sha256_bytes(content_bytes),
        "action_boundary": "vapi_chat_input",
        "resume_token": f"{branch}:{index}:{sha256_bytes(content_bytes)}",
    }


def materialize(row: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, str | None, str | None]:
    turns = row["prompt_turn_sequence"]
    if (
        turns
        and all(isinstance(turn, dict) and set(turn) == {"content", "role"} for turn in turns)
    ):
        unsupported_roles = sorted(
            {turn["role"] for turn in turns if turn["role"] != "user"}
        )
        if unsupported_roles:
            return (
                None,
                "vapi_chat_input_role_for_" + "_and_".join(unsupported_roles),
                "Vapi adapter owner",
            )
    if len(turns) != 1 or not isinstance(turns[0], dict):
        return None, "source_turn_shape", "Scanner Engineer #2"
    source = turns[0]
    keys = frozenset(source)

    if keys == {"prompt_text"}:
        branches = [{"branch_id": "main", "steps": [step("main", 1, source["prompt_text"])]}]
    elif keys == {"query"}:
        branches = [{"branch_id": "main", "steps": [step("main", 1, source["query"])]}]
    elif keys == {"prior_turn", "query"}:
        branches = [{
            "branch_id": "main",
            "steps": [
                step("main", 1, source["prior_turn"]),
                step("main", 2, source["query"]),
            ],
        }]
    elif keys == {"prompt_a", "prompt_b"}:
        branches = [
            {"branch_id": "a", "steps": [step("a", 1, source["prompt_a"])]},
            {"branch_id": "b", "steps": [step("b", 1, source["prompt_b"])]},
        ]
    elif keys == {"axis", "pair_a", "pair_b", "scenario_id"}:
        branches = [
            {"branch_id": "a", "steps": [step("a", 1, source["pair_a"])]},
            {"branch_id": "b", "steps": [step("b", 1, source["pair_b"])]},
        ]
    elif keys == {"final_query", "user_turns_a", "user_turns_b"}:
        branches = []
        for branch_id, field in (("a", "user_turns_a"), ("b", "user_turns_b")):
            values = [*source[field], source["final_query"]]
            if not all(isinstance(value, str) and value for value in values):
                return None, "authored_user_turn_scalar", "Probe-mapping owner"
            branches.append({
                "branch_id": branch_id,
                "steps": [
                    step(branch_id, index, value)
                    for index, value in enumerate(values, 1)
                ],
            })
    else:
        return (
            None,
            "authored_role_and_action_mapping_for_structured_probe_fields",
            "Probe-mapping owner",
        )

    for branch in branches:
        for item in branch["steps"]:
            if not isinstance(item["content"], str) or not item["content"]:
                return None, "nonempty_utf8_text_input", "Probe-mapping owner"
    return branches, None, None


def build(source: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source_bytes = source.read_bytes()
    source_sha = sha256_bytes(source_bytes)
    if source_sha != EXPECTED_SOURCE_SHA256:
        raise SystemExit(f"frozen inventory SHA mismatch: {source_sha}")
    all_rows = [json.loads(line) for line in source_bytes.splitlines() if line.strip()]
    rows = [row for row in all_rows if row["authorization_class"] == "structurally_non_runnable"]
    if len(rows) != 502:
        raise SystemExit(f"expected 502 structurally constrained rows, got {len(rows)}")

    records = []
    manifest_count = 0
    role_bearing_count = 0
    for row in rows:
        branches, missing_primitive, owner = materialize(row)
        source_turns_sha = sha256_bytes(canonical_bytes(row["prompt_turn_sequence"]))
        record = {
            "ordinal": row["ordinal"],
            "stable_case_id": row["stable_case_id"],
            "family_id": row["family_id"],
            "source_row_sha256": row["source_row_sha256"],
            "source_turns_sha256": source_turns_sha,
            "classification_group": (
                "privacy_minimization_reidentification_role_bearing"
                if row["family_id"] == "privacy_minimization_reidentification"
                else "original_structural"
            ),
        }
        if record["classification_group"].endswith("role_bearing"):
            role_bearing_count += 1
        if branches is None:
            record |= {
                "capability": "genuine_impossibility",
                "missing_primitive": missing_primitive,
                "remediation_owner": owner,
                "transport_manifest": None,
            }
        else:
            manifest = {
                "schema": "backbond.vapi.exact_transport_manifest.v1",
                "immutable": True,
                "conversation_isolation": "one_vapi_chat_per_branch",
                "source_turns_sha256": source_turns_sha,
                "branches": branches,
            }
            manifest["manifest_sha256"] = sha256_bytes(canonical_bytes(manifest))
            record |= {
                "capability": "executable_exact_transport",
                "missing_primitive": None,
                "remediation_owner": None,
                "transport_manifest": manifest,
            }
            manifest_count += 1
        records.append(record)

    summary = {
        "artifact_type": "capability_ledger_summary",
        "ticket_ref": "BEAAA-19284",
        "contract_ref": "contracts/are-scanner-api-v1.md@v1.3.0",
        "classification": "rule-execution",
        "scanner_version": SCANNER_VERSION,
        "corpus_version": CORPUS_SHA256,
        "probe_set_version": "vapi-freeze-v1.4",
        "rubric_point_id": "not_applicable_transport_only",
        "seed": SEED,
        "n5_calibration_delta": "0.0000 (derived transport only; no scoring or harness path changed)",
        "source": str(source.relative_to(ROOT)),
        "source_sha256": source_sha,
        "counts": {
            "structurally_constrained": len(records),
            "original_structural": len(records) - role_bearing_count,
            "privacy_minimization_reidentification_role_bearing": role_bearing_count,
            "executable_exact_transport": manifest_count,
            "genuine_impossibility": len(records) - manifest_count,
            "vapi_requests": 0,
        },
        "rollback": "Delete artifacts/beaaa-19284; frozen inventory is unchanged.",
    }
    return summary, records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    summary, records = build(args.source)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "vapi-exact-transport-summary-v1.0.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    )
    (args.out / "vapi-exact-transport-ledger-v1.0.json").write_text(
        json.dumps({"summary": summary, "records": records}, indent=2, ensure_ascii=False) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
