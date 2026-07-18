#!/usr/bin/env python3
"""Generate or fail-closed validate the BEAAA-15476 Batch 1 manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

RUNNER_COMMIT = "784778473d3e"
PLAN_REVISION_COMMENT_ID = "e56f7865-d14e-4605-a780-090c8bc6c10d"
PLAN_ATTACHMENT_SHA256 = "956579546ed88b6c5e19d27fcf159322b0543384a54abfc01d8a16e41e98ce93"
EXPECTED_CASES = 90
CORE_FAMILIES = {
    "p1_1_factuality_golden",
    "p1_3_consistency_golden",
    "p1_4_refusal_golden",
    "p1_5_calibration_golden",
}
SCANNER_VERSION = "0.4.1"
CORPUS_VERSION = "2026.6.24"
TARGET_MODEL_PIN = "syllable-insurance-demo@apichat.syllable.cloud"
JUDGE_MODEL_PIN = "anthropic:claude-sonnet-4-6"
SEED_RECIPE = {"calibration_seeds": [0, 1, 2, 3, 4], "execution_seed": 42}


def run(*args: str, cwd: Path | None = None) -> bytes:
    return subprocess.check_output(args, cwd=cwd)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pinned_source(path: str) -> bytes:
    return run("git", "show", f"{RUNNER_COMMIT}:{path}")


def build_expected(repo_root: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="beaaa15476-") as tmp:
        archive = Path(tmp) / "source.tar"
        archive.write_bytes(run("git", "archive", RUNNER_COMMIT, cwd=repo_root))
        source_root = Path(tmp) / "source"
        source_root.mkdir()
        with tarfile.open(archive) as handle:
            handle.extractall(source_root, filter="data")

        sys.path.insert(0, str(source_root))
        try:
            from tools.active_test_registry import build_registry
            from tools.text_case_materializer import materialize_text_case, text_case_identity

            eligible = []
            for row in build_registry():
                if row.get("status") != "active" or row.get("target_bound") is not True:
                    continue
                if row["test_id"] in CORE_FAMILIES:
                    continue
                try:
                    spec = materialize_text_case(row["test_id"], row["case_id"], row)
                except (KeyError, TypeError, ValueError):
                    continue
                eligible.append((row, spec, text_case_identity(spec)))
        finally:
            sys.path.pop(0)

    selected = []
    collisions = []
    first_by_identity: dict[str, str] = {}
    for row, spec, identity in eligible:
        if identity in first_by_identity:
            collisions.append(
                {
                    "duplicate_case_id": row["case_id"],
                    "first_case_id": first_by_identity[identity],
                    "normalized_identity_sha256": identity,
                }
            )
            continue
        first_by_identity[identity] = row["case_id"]
        source_path = row["source_pack"]
        selected.append(
            {
                "selection_index": len(selected) + 1,
                "case_id": row["case_id"],
                "test_id": row["test_id"],
                "source_path": source_path,
                "source_sha256": sha256(pinned_source(source_path)),
                "source_commit": RUNNER_COMMIT,
                "rubric_point_id": row["risk_or_rubric_id"],
                "target_call_count": len(spec.prompts),
                "judge_call_count": 1,
                "prompts": list(spec.prompts),
                "expected_oracle": spec.expected,
                "normalized_identity_sha256": identity,
                "scanner_version": SCANNER_VERSION,
                "corpus_version": CORPUS_VERSION,
                "model_pins": {
                    "target": TARGET_MODEL_PIN,
                    "judge": JUDGE_MODEL_PIN,
                    "judge_temperature": 0,
                },
                "seed_recipe": SEED_RECIPE,
            }
        )

    return {
        "schema_version": "beaaa-15476.batch1-activation-manifest.v1",
        "ticket": "BEAAA-15476",
        "contract": "are-scanner-api-v1@v1.0",
        "plan_revision": {
            "paperclip_comment_id": PLAN_REVISION_COMMENT_ID,
            "attachment_sha256": PLAN_ATTACHMENT_SHA256,
            "version": "Plan A v0.2",
        },
        "selection": {
            "runner_commit": RUNNER_COMMIT,
            "rule": "canonical mapping order; exclude live core families; retain first occurrence of each normalized (prompts, expected) identity",
            "eligible_row_count": len(eligible),
            "selected_case_count": len(selected),
            "excluded_collision_count": len(collisions),
            "live_target_calls_used_to_freeze": 0,
            "judge_calls_used_to_freeze": 0,
        },
        "pins": {
            "scanner_version": SCANNER_VERSION,
            "corpus_version": CORPUS_VERSION,
            "target_model": TARGET_MODEL_PIN,
            "judge_model": JUDGE_MODEL_PIN,
            "judge_temperature": 0,
            "seed_recipe": SEED_RECIPE,
        },
        "cases": selected,
        "excluded_collisions": collisions,
    }


def canonical_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def validate(manifest: dict, expected: dict) -> None:
    cases = manifest.get("cases") or []
    case_ids = [row.get("case_id") for row in cases]
    identities = [row.get("normalized_identity_sha256") for row in cases]
    errors = []
    if len(cases) != EXPECTED_CASES:
        errors.append(f"expected {EXPECTED_CASES} rows, found {len(cases)}")
    if len(set(case_ids)) != EXPECTED_CASES:
        errors.append("case_id values are not exactly 90 unique values")
    if len(set(identities)) != EXPECTED_CASES:
        errors.append("normalized (prompts, expected) identities are not exactly 90 unique values")
    required = {
        "source_path", "source_sha256", "rubric_point_id", "target_call_count",
        "judge_call_count", "expected_oracle", "scanner_version", "corpus_version",
        "model_pins", "seed_recipe",
    }
    for index, row in enumerate(cases, 1):
        missing = sorted(key for key in required if key not in row)
        if missing:
            errors.append(f"row {index} missing {missing}")
        try:
            observed = sha256(pinned_source(row["source_path"]))
            if observed != row["source_sha256"]:
                errors.append(f"row {index} source hash mismatch")
        except Exception as exc:
            errors.append(f"row {index} source validation failed: {exc}")
    if canonical_bytes(manifest) != canonical_bytes(expected):
        errors.append("manifest does not byte-canonically match the pinned first-occurrence selection")
    if errors:
        raise SystemExit("FAIL CLOSED:\n- " + "\n- ".join(errors))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    repo_root = Path(run("git", "rev-parse", "--show-toplevel").decode().strip())
    expected = build_expected(repo_root)
    if args.write:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_bytes(canonical_bytes(expected))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    validate(manifest, expected)
    print(
        f"PASS: {EXPECTED_CASES} unique case_ids; {EXPECTED_CASES} unique normalized identities; "
        f"{expected['selection']['excluded_collision_count']} collisions excluded; source hashes match {RUNNER_COMMIT}"
    )


if __name__ == "__main__":
    main()
