#!/usr/bin/env python3
"""Derive the BEAAA-19287 v1.4 remediation freeze without sending probe traffic."""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "artifacts/beaaa-19218/vapi-2000-probe-authorization-freeze-v1.3.jsonl"
DEFAULT_OUT = ROOT / "artifacts/beaaa-19287"
SOURCE_SHA256 = "d483a281556d9ae04a09bccc00bd3f886f27cce2f2dbde4534862c731e818319"
CORPUS_SHA256 = "fa32e0e081ffbd2953ea3df8aa8d2d0faa0464a3076d17dad9b1758804488988"
SCANNER_VERSION = "0.5.0"
SEED = 42


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def transport_step(branch_id: str, step_index: int, content: str) -> dict[str, Any]:
    content_sha = sha256_bytes(content.encode("utf-8"))
    return {
        "branch_id": branch_id,
        "step_index": step_index,
        "transport_role": "user",
        "content": content,
        "content_utf8_sha256": content_sha,
        "action_boundary": "vapi_chat_input",
        "resume_token": f"{branch_id}:{step_index}:{content_sha}",
    }


def exact_text_materialization(
    row: dict[str, Any],
) -> tuple[list[dict[str, Any]] | None, str]:
    """Return an exact current-transport manifest, or a capable-adapter reason."""
    turns = row["prompt_turn_sequence"]
    if len(turns) != 1 or not isinstance(turns[0], dict):
        return None, "source_turn_shape_requires_role_or_action_preserving_adapter"
    source = turns[0]
    keys = frozenset(source)

    if keys == {"prompt_text"}:
        branch_values = {"main": [source["prompt_text"]]}
    elif keys == {"query"}:
        branch_values = {"main": [source["query"]]}
    elif keys == {"prior_turn", "query"}:
        branch_values = {"main": [source["prior_turn"], source["query"]]}
    elif keys == {"prompt_a", "prompt_b"}:
        branch_values = {"a": [source["prompt_a"]], "b": [source["prompt_b"]]}
    elif keys == {"axis", "pair_a", "pair_b", "scenario_id"}:
        branch_values = {"a": [source["pair_a"]], "b": [source["pair_b"]]}
    elif keys == {"final_query", "user_turns_a", "user_turns_b"}:
        branch_values = {
            "a": [*source["user_turns_a"], source["final_query"]],
            "b": [*source["user_turns_b"], source["final_query"]],
        }
    else:
        return None, "structured_fields_require_authored_semantic_adapter"

    if not all(
        isinstance(value, str) and value
        for values in branch_values.values()
        for value in values
    ):
        return None, "nonempty_utf8_text_requires_authored_semantic_adapter"

    return [
        {
            "branch_id": branch_id,
            "steps": [
                transport_step(branch_id, index, value)
                for index, value in enumerate(values, 1)
            ],
        }
        for branch_id, values in branch_values.items()
    ], ""


def build(source: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source_sha = sha256_file(source)
    if source_sha != SOURCE_SHA256:
        raise SystemExit(f"v1.3_sha256_mismatch:{source_sha}")

    all_rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    if len(all_rows) != 2000:
        raise SystemExit(f"full_registry_row_count_mismatch:{len(all_rows)}")
    source_classes = collections.Counter(row["authorization_class"] for row in all_rows)
    selected = [
        row for row in all_rows
        if row["authorization_class"] == "structurally_non_runnable"
    ]
    if len(selected) != 477:
        raise SystemExit(f"v1.3_structural_count_mismatch:{len(selected)}")
    if len({row["stable_case_id"] for row in selected}) != 477:
        raise SystemExit("v1.3_structural_stable_id_uniqueness_failure")

    records: list[dict[str, Any]] = []
    dispositions: collections.Counter[str] = collections.Counter()
    adapter_reasons: collections.Counter[str] = collections.Counter()
    for row in selected:
        branches, adapter_reason = exact_text_materialization(row)
        source_turns_sha = sha256_bytes(canonical_bytes(row["prompt_turn_sequence"]))
        record: dict[str, Any] = {
            "schema": "backbond.vapi_remediation_freeze/v1.4",
            "ticket_ref": "BEAAA-19287",
            "scanner_version": SCANNER_VERSION,
            "corpus_version": CORPUS_SHA256,
            "probe_set_version": "vapi-remediation-freeze-v1.4",
            "rubric_point_id": "not_applicable_transport_materialization",
            "seed": SEED,
            "ordinal": row["ordinal"],
            "stable_case_id": row["stable_case_id"],
            "family_id": row["family_id"],
            "source_row_sha256": row["source_row_sha256"],
            "source_turns_sha256": source_turns_sha,
            "source_authorization_class": row["authorization_class"],
        }
        if branches is not None:
            manifest = {
                "schema": "backbond.vapi.exact_text_transport/v1",
                "immutable": True,
                "conversation_isolation": "one_vapi_chat_per_branch",
                "source_turns_sha256": source_turns_sha,
                "branches": branches,
            }
            manifest["manifest_sha256"] = sha256_bytes(canonical_bytes(manifest))
            record |= {
                "disposition": "faithfully_materialized_current_text_transport",
                "reason": "Every emitted UTF-8 input is copied byte-for-byte from an authored source text field.",
                "route": {
                    "status": "eligible_unchanged",
                    "controlled_execution_lane": "BEAAA-19220",
                    "probe_requests_emitted": 0,
                },
                "required_adapter": None,
                "transport_manifest": manifest,
            }
        else:
            record |= {
                "disposition": "requires_different_capable_adapter",
                "reason": adapter_reason,
                "route": {
                    "status": "fail_closed",
                    "controlled_execution_lane": None,
                    "probe_requests_emitted": 0,
                },
                "required_adapter": {
                    "capability": "authored_structured_semantics_preservation",
                    "must_preserve": [
                        "field_roles",
                        "oracle_inputs",
                        "action_boundaries",
                        "source_bytes",
                    ],
                    "authorization_inference_permitted": False,
                },
                "transport_manifest": None,
            }
            adapter_reasons[adapter_reason] += 1
        dispositions[record["disposition"]] += 1
        records.append(record)

    full_registry = dict(sorted(source_classes.items()))
    full_registry.pop("structurally_non_runnable")
    full_registry.update(dict(sorted(dispositions.items())))
    summary = {
        "schema": "backbond.vapi_remediation_freeze_summary/v1.4",
        "artifact_type": "internal_scanner_transport_freeze",
        "ticket_ref": "BEAAA-19287",
        "parent_refs": ["BEAAA-19286", "BEAAA-19217"],
        "contract_ref": "contracts/are-scanner-api-v1.md@v1.3.0",
        "classification": "rule-execution",
        "scanner_version": SCANNER_VERSION,
        "corpus_version": CORPUS_SHA256,
        "probe_set_version": "vapi-remediation-freeze-v1.4",
        "rubric_point_id": "not_applicable_transport_materialization",
        "seed": SEED,
        "n5_calibration_delta": "0.0000",
        "cto_cosign_ref": None,
        "source": {
            "path": str(source.relative_to(ROOT)),
            "row_count": len(all_rows),
            "sha256": source_sha,
            "immutable_v1_3_verified": True,
        },
        "terminal_accounting_477": dict(sorted(dispositions.items())),
        "adapter_reason_counts": dict(sorted(adapter_reasons.items())),
        "full_registry_accounting_2000": full_registry,
        "unique_stable_ids": len({row["stable_case_id"] for row in records}),
        "zero_probe_requests_emitted": True,
        "duplicate_suppression_preserved": True,
        "stop_controls_preserved": True,
        "rollback": "Discard this derived v1.4 remediation freeze; retain immutable v1.3.",
    }
    if sum(dispositions.values()) != 477:
        raise SystemExit("terminal_accounting_mismatch")
    if sum(full_registry.values()) != 2000:
        raise SystemExit("full_registry_accounting_mismatch")
    return summary, records


def write_artifacts(source: Path, out: Path) -> tuple[Path, Path]:
    summary, records = build(source)
    out.mkdir(parents=True, exist_ok=True)
    freeze_path = out / "vapi-477-remediation-freeze-v1.4.jsonl"
    summary_path = out / "vapi-477-remediation-freeze-v1.4.summary.json"
    freeze_path.write_bytes(b"".join(canonical_bytes(record) + b"\n" for record in records))
    summary["freeze"] = {
        "path": "artifacts/beaaa-19287/vapi-477-remediation-freeze-v1.4.jsonl",
        "row_count": len(records),
        "sha256": sha256_file(freeze_path),
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return freeze_path, summary_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    write_artifacts(args.source, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
