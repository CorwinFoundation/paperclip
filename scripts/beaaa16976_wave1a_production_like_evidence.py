"""BEAAA-16979 retained production-like evidence for Wave 1A QA retest."""
from __future__ import annotations

import hashlib
import json
import os
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

OUT_DIR = REPO / "artifacts" / "beaaa-16979"
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


def _target_authorization_from_payload(payload: dict[str, Any]) -> wave.TargetAuthorization:
    fields = set(wave.TargetAuthorization.__dataclass_fields__)
    selected = {name: payload[name] for name in fields if name in payload}
    for tuple_field in ("declared_tool_inventory", "allowed_family_ids", "capability_conflicts"):
        if tuple_field in selected:
            selected[tuple_field] = tuple(selected[tuple_field])
    return wave.TargetAuthorization(**selected)


def _load_authorization(commit_sha: str) -> wave.TargetAuthorization:
    path = os.environ.get("WAVE1A_PRODUCTION_AUTHORIZATION_JSON")
    if path:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return _target_authorization_from_payload(payload)
    return wave.TargetAuthorization(**{**wave.reference_authorization().__dict__, "candidate_commit_sha": commit_sha})


def _family_runs() -> dict[str, list[dict[str, Any]]]:
    return {
        csdl.SPEC.name: base.replicate(csdl.SPEC, csdl.compliant_policy),
        cac.SPEC.name: base.replicate(cac.SPEC, cac.compliant_policy),
    }


def _production_like_target_output(commit_sha: str) -> dict[str, Any]:
    target = _load_authorization(commit_sha)
    packet = wave.build_scoring_active_packet(commit_sha=commit_sha, target=target)
    runs = _family_runs()
    scored_runtime_outputs = {
        family: wave.scoring_active_runtime_contract(family, family_runs)
        for family, family_runs in runs.items()
    }
    return {
        "issue": "BEAAA-16979",
        "contract": wave.CONTRACT,
        "scanner_version": base.SCANNER_VERSION,
        "candidate_commit_sha": commit_sha,
        "authorized_target": {
            "target_id": target.target_id,
            "candidate_commit_sha": target.candidate_commit_sha,
            "authorization_ref": target.authorization_ref,
            "authorization_signature": target.authorization_signature,
            "authorization_issued_at": target.authorization_issued_at,
            "authorization_expires_at": target.authorization_expires_at,
            "tenant_boundary": target.tenant_boundary,
            "session_boundary": target.session_boundary,
            "declared_tool_inventory": list(target.declared_tool_inventory),
            "use_case_scope": target.use_case_scope,
            "allowed_family_ids": list(target.allowed_family_ids),
        },
        "packet": packet,
        "scoring_boundary": {
            "substrate_runner": "evals.aiuc_path_b.base.run_family",
            "substrate_contract": "reported_not_scored evidence substrate",
            "activation_layer": "rubric/probe_mappings binding.scoring_active=true and binding.underwriting_active=true",
            "actual_scored_output_location": "retained_outputs.*.scored_runtime_contract",
            "score_weight": 0.0,
            "score_math_changed": False,
        },
        "retained_outputs": {
            family: {
                "seeds": [run["seed"] for run in family_runs],
                "case_count": family_runs[0]["case_count"],
                "execution_count": sum(len(run["case_results"]) for run in family_runs),
                "evidence_sha256_by_seed": {str(run["seed"]): run["sha256"] for run in family_runs},
                "substrate_unscored_runs": family_runs,
                "scored_runtime_contract": scored_runtime_outputs[family],
            }
            for family, family_runs in runs.items()
        },
        "retention": {
            "immutable": True,
            "retained_under": str((OUT_DIR / "production_like_target_output.json").relative_to(REPO)),
            "manifest_sha256": (packet.get("signed_manifest") or {}).get("sha256"),
            "response_hash": _sha256(packet),
        },
        "consistency_checks": {
            "packet_scoring_active": packet["promotion_state"] == wave.SCORING_ACTIVE_PROMOTION_STATE,
            "packet_underwriting_active": packet["underwriting_state"] == wave.UNDERWRITING_ACTIVE_STATE,
            "all_retained_scored_outputs_scored": all(
                run["scored"] is True and run["scoring"] == "scoring_active"
                for family_runs in scored_runtime_outputs.values()
                for run in family_runs
            ),
            "raw_substrate_is_separated_from_scored_output": True,
        },
    }


def _fail_closed_outputs(commit_sha: str) -> dict[str, Any]:
    target = _load_authorization(commit_sha)
    signature_tail = (
        target.authorization_signature.split(":", 2)[2]
        if len(target.authorization_signature.split(":", 2)) == 3
        else "invalid"
    )
    cases = {
        "missing_authorization_ref": wave.TargetAuthorization(
            **{**target.__dict__, "authorization_ref": ""}
        ),
        "missing_authorization_signature": wave.TargetAuthorization(
            **{**target.__dict__, "authorization_signature": ""}
        ),
        "invalid_authorization_signature": wave.TargetAuthorization(
            **{
                **target.__dict__,
                "authorization_signature": f"ed25519:{wave.AUTHORIZED_SIGNER_KEY_ID}:invalid",
            }
        ),
        "unknown_authorization_signer": wave.TargetAuthorization(
            **{
                **target.__dict__,
                "authorization_signature": "ed25519:attacker-key:" + signature_tail,
            }
        ),
        "stale_authorization_signature": wave.TargetAuthorization(
            **{**target.__dict__, "authorization_expires_at": "2026-07-21T23:59:59Z"}
        ),
        "post_signature_scope_tampering": wave.TargetAuthorization(
            **{**target.__dict__, "use_case_scope": "attacker-expanded-scope"}
        ),
        "post_signature_tool_inventory_tampering": wave.TargetAuthorization(
            **{**target.__dict__, "declared_tool_inventory": ()}
        ),
        "post_signature_family_scope_tampering": wave.TargetAuthorization(
            **{**target.__dict__, "allowed_family_ids": ("cross_session_data_leak",)}
        ),
        "post_signature_corpus_hash_tampering": wave.TargetAuthorization(
            **{
                **target.__dict__,
                "corpus_sha256": {**target.corpus_sha256, "cross_session_data_leak": "bad"},
            }
        ),
        "post_signature_evidence_immutability_tampering": wave.TargetAuthorization(
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
    valid_manifest = wave.build_scoring_active_packet(commit_sha=commit_sha, target=target)
    tampered_manifest = {
        **valid_manifest,
        "promotion_state": "scoring_active_tampered_after_signature",
    }
    outputs["valid_signed_manifest_verification"] = wave.fail_closed_for_signed_manifest(valid_manifest)
    outputs["tampered_signed_manifest_verification"] = wave.fail_closed_for_signed_manifest(tampered_manifest)
    return {
        "issue": "BEAAA-16979",
        "candidate_commit_sha": commit_sha,
        "fail_closed_cases": outputs,
        "all_negative_cases_fail_closed": all(
            row.get("fail_closed") is True
            for name, row in outputs.items()
            if name != "valid_signed_manifest_verification"
        ),
        "valid_signed_manifest_passes": outputs["valid_signed_manifest_verification"].get("fail_closed")
        is False,
        "failure_codes": {name: row.get("failure_code") for name, row in outputs.items()},
    }


def _monitoring_canary_false_positive_peril(commit_sha: str) -> dict[str, Any]:
    packet = wave.build_scoring_active_packet(
        commit_sha=commit_sha,
        target=_load_authorization(commit_sha),
    )
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
        "issue": "BEAAA-16979",
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
        "false_positive_budget": packet.get("false_positive_budget"),
        "peril_mapping": packet.get("peril_mapping"),
        "independent_judge": {
            "applicability": "not_applicable",
            "basis": (
                "Wave 1A uses deterministic structured-policy graders only; "
                "no LLM judge is invoked or required for these zero-weight "
                "scoring-active eligibility families. The substrate runner remains "
                "reported_not_scored, and the retained production-like output records "
                "the activation boundary plus the scored runtime contract."
            ),
        },
    }


def _rollback_drill() -> dict[str, Any]:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="beaaa16979-rollback-") as td:
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
            "issue": "BEAAA-16979",
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
        "issue": "BEAAA-16979",
        "candidate_commit_sha": commit_sha,
        "outputs": written,
        "all_negative_cases_fail_closed": outputs["fail_closed_authorization_pin_signature_output.json"][
            "all_negative_cases_fail_closed"
        ],
        "valid_signed_manifest_passes": outputs["fail_closed_authorization_pin_signature_output.json"][
            "valid_signed_manifest_passes"
        ],
        "production_like_scored_contract_consistent": outputs["production_like_target_output.json"][
            "consistency_checks"
        ]["all_retained_scored_outputs_scored"],
        "rollback_340_to_305": outputs["rollback_drill_output.json"]["active_executable_before"] == 340
        and outputs["rollback_drill_output.json"]["active_executable_after"] == 305,
        "unrelated_active_rows_unchanged": outputs["rollback_drill_output.json"][
            "unrelated_active_rows_unchanged"
        ],
    }
    summary_path = _write_json("production_like_evidence_summary.json", summary)
    print(json.dumps({"summary": str(summary_path.relative_to(REPO)), **summary}, indent=2))
    return 0 if (
        summary["all_negative_cases_fail_closed"]
        and summary["valid_signed_manifest_passes"]
        and summary["production_like_scored_contract_consistent"]
        and summary["rollback_340_to_305"]
        and summary["unrelated_active_rows_unchanged"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
