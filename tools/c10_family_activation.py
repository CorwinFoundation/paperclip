#!/usr/bin/env python3
"""Validated, family-scoped C10 scored-case activation overlays."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE_MAPPING_LEDGER = (
    ROOT / "artifacts" / "BEAAA-25646" / "c10-mapping-ledger-v1.0.jsonl"
)
ACTIVATION_DIR = ROOT / "artifacts" / "BEAAA-26529"
ACTIVATION_PATHS = tuple(
    sorted(ACTIVATION_DIR.glob("c10-*-scoring-activation-v1.json"))
)
SCHEMA = "backbond-c10-family-scoring-activation@1"


class C10FamilyActivationError(RuntimeError):
    """An activation overlay or its pinned evidence failed validation."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise C10FamilyActivationError(f"cannot load activation input: {path}") from exc


def _repo_path(value: object) -> Path:
    path = (ROOT / str(value)).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise C10FamilyActivationError("activation evidence path escapes repository") from exc
    return path


def load_c10_family_activations() -> dict[tuple[str, str], dict[str, Any]]:
    """Return exact activated C10 rows after validating pinned saved evidence."""

    activated: dict[tuple[str, str], dict[str, Any]] = {}
    for activation_path in ACTIVATION_PATHS:
        manifest = _load_json(activation_path)
        if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
            raise C10FamilyActivationError("invalid C10 family activation schema")
        family = str(manifest.get("family_id") or "")
        stable_ids = tuple(str(value) for value in manifest.get("stable_case_ids") or ())
        if (
            not family
            or len(stable_ids) != manifest.get("case_count")
            or len(stable_ids) != len(set(stable_ids))
        ):
            raise C10FamilyActivationError("invalid C10 family activation roster")

        effect = manifest.get("registry_effect")
        expected_effect = {
            "reporting_status": "scored",
            "scoring_active": True,
            "score_input": True,
            "underwriting_active": False,
            "priced_score_weight": 0.0,
            "attestation_tier": "founder_dev_mode",
        }
        if effect != expected_effect:
            raise C10FamilyActivationError("C10 activation effect is not scored-zero-weight")
        if _sha256(SOURCE_MAPPING_LEDGER) != manifest.get(
            "source_mapping_ledger_sha256"
        ):
            raise C10FamilyActivationError("source C10 mapping-ledger hash mismatch")
        source_rows = [
            json.loads(line)
            for line in SOURCE_MAPPING_LEDGER.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        source_ids = {
            str(row.get("stable_case_id"))
            for row in source_rows
            if row.get("family_id") == family
        }
        if source_ids != set(stable_ids):
            raise C10FamilyActivationError(
                "activation roster does not match source C10 mapping ledger"
            )

        evidence = manifest.get("evidence")
        if not isinstance(evidence, dict):
            raise C10FamilyActivationError("C10 activation evidence block is missing")
        backlog_path = _repo_path(evidence.get("canonical_backlog_path"))
        scoreability_path = _repo_path(evidence.get("scoreability_ledger_path"))
        if _sha256(backlog_path) != evidence.get("canonical_backlog_sha256"):
            raise C10FamilyActivationError("canonical backlog hash mismatch")
        if _sha256(scoreability_path) != evidence.get("scoreability_ledger_sha256"):
            raise C10FamilyActivationError("scoreability ledger hash mismatch")

        backlog_rows = [
            json.loads(line)
            for line in backlog_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        backlog = {
            str(row.get("stable_case_id")): row
            for row in backlog_rows
            if row.get("family_id") == family
        }
        scoreability_data = _load_json(scoreability_path)
        scoreability_rows = scoreability_data.get("rows") or scoreability_data.get("records")
        if not isinstance(scoreability_rows, list):
            raise C10FamilyActivationError("scoreability ledger rows are missing")
        scoreability = {
            str(row.get("stable_case_id")): row
            for row in scoreability_rows
            if row.get("family_id") == family
        }
        if set(backlog) != set(stable_ids) or set(scoreability) != set(stable_ids):
            raise C10FamilyActivationError("activation roster does not match pinned evidence")

        for stable_id in stable_ids:
            backlog_row = backlog[stable_id]
            scoreability_row = scoreability[stable_id]
            verdict = str(backlog_row.get("verdict") or "")
            if (
                backlog_row.get("status") != "deterministic_valid"
                or backlog_row.get("eligibility_class") != "current_score_input_eligible"
                or verdict not in {"PASS", "FAIL"}
                or scoreability_row.get("execution_status") != "executed"
                or scoreability_row.get("final_verdict") != verdict
                or not scoreability_row.get("deterministic_result_sha256")
            ):
                raise C10FamilyActivationError(
                    f"saved deterministic evidence is not scoreable: {family}:{stable_id}"
                )
            key = (family, stable_id)
            if key in activated:
                raise C10FamilyActivationError(f"duplicate activated C10 row: {key}")
            activated[key] = {
                **expected_effect,
                "activation_ref": str(manifest.get("activation_ref") or ""),
                "c10_family_activation": True,
                "c10_activation_manifest": activation_path.relative_to(ROOT).as_posix(),
                "saved_verdict": verdict,
                "saved_evidence_ref": str(backlog_row.get("evidence", {}).get("execution") or ""),
                "saved_result_ref": str(backlog_row.get("evidence", {}).get("result") or ""),
                "dev_scoring_active": True,
            }
    return activated


def apply_c10_family_activations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply each exact activation only to its content-inventory registry row."""

    activated = load_c10_family_activations()
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (str(row.get("test_id") or ""), str(row.get("case_id") or ""))
        effect = activated.get(key)
        if effect is None:
            continue
        if row.get("content_candidate_inventory") is not True:
            raise C10FamilyActivationError(f"activation target is not a C10 inventory row: {key}")
        row.update(effect)
        row.update(
            {
                "production_runner_executable": True,
                "target_bound": True,
                "execution_evidenced": True,
                "execution_evidence_status": "saved_vapi_deterministic_verdict_bound",
                "canonical_runtime_case": True,
                "activation_blocker": None,
                "candidate_registration_status": "registered_scored_zero_weight",
                "candidate_rubric_mapping_status": "mapped_scored_zero_weight",
                "effective_underwriting_weight": 0.0,
            }
        )
        seen.add(key)
    if seen != set(activated):
        missing = sorted(set(activated) - seen)
        raise C10FamilyActivationError(f"activated C10 rows are absent from registry: {missing}")
    return rows
