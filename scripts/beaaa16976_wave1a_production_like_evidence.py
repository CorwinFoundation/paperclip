"""BEAAA-16976 retained production-like evidence for Wave 1A QA retest."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from shutil import copytree
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from evals.aiuc_path_b import base  # noqa: E402
from evals.aiuc_path_b import consequential_action_confirmation as cac  # noqa: E402
from evals.aiuc_path_b import cross_session_data_leak as csdl  # noqa: E402
from evals.aiuc_path_b import wave1a_candidate as wave  # noqa: E402
from tools.active_test_registry import (  # noqa: E402
    AIUC_PATH_B_SCORING_ACTIVE_FAMILIES,
    build_registry,
    count_active_executable,
)

OUT_DIR = REPO / "artifacts" / "beaaa-16976"
MAPPING_DIR = REPO / "rubric" / "probe_mappings"


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(obj: Any) -> str:
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


def _write_json(name: str, payload: dict[str, Any]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _git_sha() -> str:
    import subprocess

    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()


def _family_runs() -> dict[str, list[dict[str, Any]]]:
    return {
        csdl.SPEC.name: base.replicate(csdl.SPEC, csdl.compliant_policy),
        cac.SPEC.name: base.replicate(cac.SPEC, cac.compliant_policy),
    }


def _production_like_target_output(commit_sha: str) -> dict[str, Any]:
    target = wave.reference_authorization()
    packet = wave.build_scoring_active_packet(commit_sha=commit_sha)
    runs = _family_runs()
    return {
        "issue": "BEAAA-16976",
        "contract": wave.CONTRACT,
        "scanner_version": base.SCANNER_VERSION,
        "candidate_commit_sha": commit_sha,
        "authorized_target": {
            "target_id": target.target_id,
            "authorization_ref": target.authorization_ref,
            "authorization_signature": target.authorization_signature,
            "tenant_boundary": target.tenant_boundary,
            "session_boundary": target.session_boundary,
            "declared_tool_inventory": list(target.declared_tool_inventory),
            "use_case_scope": target.use_case_scope,
            "allowed_family_ids": list(target.allowed_family_ids),
        },
        "packet": packet,
        "retained_outputs": {
            family: {
                "seeds": [run["seed"] for run in family_runs],
                "case_count": family_runs[0]["case_count"],
                "execution_count": sum(len(run["case_results"]) for run in family_runs),
                "evidence_sha256_by_seed": {str(run["seed"]): run["sha256"] for run in family_runs},
                "raw_runs": family_runs,
            }
            for family, family_runs in runs.items()
        },
        "retention": {
            "immutable": True,
            "retained_under": str((OUT_DIR / "production_like_target_output.json").relative_to(REPO)),
            "manifest_sha256": packet["signed_manifest"]["sha256"],
            "response_hash": _sha256(packet),
        },
    }


def _fail_closed_outputs(commit_sha: str) -> dict[str, Any]:
    target = wave.reference_authorization()
    cases = {
        "missing_authorization": wave.TargetAuthorization(
            **{**target.__dict__, "authorization_ref": ""}
        ),
        "missing_tool_inventory": wave.TargetAuthorization(
            **{**target.__dict__, "declared_tool_inventory": ()}
        ),
        "family_scope_conflict": wave.TargetAuthorization(
            **{**target.__dict__, "allowed_family_ids": ("cross_session_data_leak",)}
        ),
        "corpus_hash_mismatch": wave.TargetAuthorization(
            **{
                **target.__dict__,
                "corpus_sha256": {**target.corpus_sha256, "cross_session_data_leak": "bad"},
            }
        ),
        "mutable_evidence": wave.TargetAuthorization(
            **{**target.__dict__, "evidence_immutable": False}
        ),
    }
    outputs = {
        name: wave.build_candidate_packet(target=case_target, commit_sha=commit_sha)
        for name, case_target in cases.items()
    }
    outputs["incomplete_runs"] = wave.build_candidate_packet(
        target=target,
        commit_sha=commit_sha,
        force_incomplete_family="cross_session_data_leak",
    )
    return {
        "issue": "BEAAA-16976",
        "candidate_commit_sha": commit_sha,
        "fail_closed_cases": outputs,
        "all_fail_closed": all(row.get("fail_closed") is True for row in outputs.values()),
        "failure_codes": {name: row.get("failure_code") for name, row in outputs.items()},
    }


def _monitoring_canary_false_positive_peril(commit_sha: str) -> dict[str, Any]:
    packet = wave.build_scoring_active_packet(commit_sha=commit_sha)
    runs = _family_runs()
    canary_rows = {
        family: [
            row
            for run in family_runs
            for row in run["case_results"]
            if row["is_canary"]
        ]
        for family, family_runs in runs.items()
    }
    reckless = {
        csdl.SPEC.name: base.verdict_tally(base.run_family(csdl.SPEC, csdl.reckless_policy, seed=0)),
        cac.SPEC.name: base.verdict_tally(base.run_family(cac.SPEC, cac.reckless_policy, seed=0)),
    }
    return {
        "issue": "BEAAA-16976",
        "candidate_commit_sha": commit_sha,
        "canary": {
            "records": {family: [row["case_id"] for row in rows] for family, rows in canary_rows.items()},
            "compliant_verdicts": {
                family: sorted({row["verdict"] for row in rows})
                for family, rows in canary_rows.items()
            },
            "reckless_policy_tally_seed0": reckless,
            "trips_metric_gaming_behavior": {
                family: reckless[family].get(base.FAIL, 0) > 0 for family in reckless
            },
        },
        "monitoring": {
            "registry_count_gate_expected": 340,
            "duplicate_runtime_identity_drift_gate": "covered by tests/test_active_test_registry_beaaa13644.py",
            "mapping_registry_disagreement_gate": "covered by tests/test_beaaa16972_wave1a_scored_activation.py",
            "fail_closed_controls_gate": "covered by tests/test_beaaa16945_wave1a_candidate.py",
        },
        "false_positive_budget": packet["false_positive_budget"],
        "peril_mapping": packet["peril_mapping"],
        "independent_judge": {
            "applicability": "not_applicable",
            "basis": (
                "Wave 1A uses deterministic structured-policy graders only; "
                "no LLM judge is invoked or required for these reported-not-scored "
                "P0 eligibility/referral families."
            ),
        },
    }


def _rollback_drill() -> dict[str, Any]:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="beaaa16976-rollback-") as td:
        mapping_dir = Path(td) / "probe_mappings"
        copytree(MAPPING_DIR, mapping_dir)
        before_rows = build_registry(mapping_dir)
        unrelated_before = {
            (row["test_id"], row["case_id"], row["risk_or_rubric_id"]): row
            for row in before_rows
            if row["test_id"] not in AIUC_PATH_B_SCORING_ACTIVE_FAMILIES
            and row["status"] == "active"
            and row["production_runner_executable"] is True
        }
        for family in AIUC_PATH_B_SCORING_ACTIVE_FAMILIES:
            path = mapping_dir / f"{family}.yaml"
            row = yaml.safe_load(path.read_text(encoding="utf-8"))
            row["reporting_status"] = "reported_not_scored"
            row["scoring"] = "reported_not_scored"
            binding = dict(row.get("binding") or {})
            binding["scoring_active"] = False
            binding["underwriting_active"] = False
            row["binding"] = binding
            path.write_text(yaml.safe_dump(row, sort_keys=False), encoding="utf-8")
        after_rows = build_registry(mapping_dir)
        unrelated_after = {
            (row["test_id"], row["case_id"], row["risk_or_rubric_id"]): row
            for row in after_rows
            if row["test_id"] not in AIUC_PATH_B_SCORING_ACTIVE_FAMILIES
            and row["status"] == "active"
            and row["production_runner_executable"] is True
        }
        return {
            "issue": "BEAAA-16976",
            "active_executable_before": count_active_executable(before_rows),
            "active_executable_after": count_active_executable(after_rows),
            "target_bound_runtime_before": len(before_rows),
            "target_bound_runtime_after": len(after_rows),
            "unrelated_active_rows_unchanged": unrelated_after == unrelated_before,
            "rolled_back_families": sorted(AIUC_PATH_B_SCORING_ACTIVE_FAMILIES),
        }


def main() -> int:
    commit_sha = _git_sha()
    outputs = {
        "production_like_target_output.json": _production_like_target_output(commit_sha),
        "fail_closed_authorization_pin_signature_output.json": _fail_closed_outputs(commit_sha),
        "canary_monitoring_retention_peril_output.json": _monitoring_canary_false_positive_peril(commit_sha),
        "rollback_drill_output.json": _rollback_drill(),
    }
    written = {name: str(_write_json(name, payload).relative_to(REPO)) for name, payload in outputs.items()}
    summary = {
        "issue": "BEAAA-16976",
        "candidate_commit_sha": commit_sha,
        "outputs": written,
        "all_fail_closed": outputs["fail_closed_authorization_pin_signature_output.json"]["all_fail_closed"],
        "rollback_340_to_305": outputs["rollback_drill_output.json"]["active_executable_before"] == 340
        and outputs["rollback_drill_output.json"]["active_executable_after"] == 305,
        "unrelated_active_rows_unchanged": outputs["rollback_drill_output.json"][
            "unrelated_active_rows_unchanged"
        ],
    }
    summary_path = _write_json("production_like_evidence_summary.json", summary)
    print(json.dumps({"summary": str(summary_path.relative_to(REPO)), **summary}, indent=2))
    return 0 if summary["all_fail_closed"] and summary["rollback_340_to_305"] and summary["unrelated_active_rows_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
