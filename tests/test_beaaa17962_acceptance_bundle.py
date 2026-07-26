from __future__ import annotations

import copy
import json
from pathlib import Path

from tools.validate_beaaa17962_acceptance_bundle import validate_bundle


BUNDLE = Path("artifacts/beaaa-17962/beaaa-17386-acceptance-evidence-bundle.json")


def _write_tmp(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _bundle_data() -> dict:
    return json.loads(BUNDLE.read_text(encoding="utf-8"))


def test_beaaa17962_bundle_validates() -> None:
    assert validate_bundle(BUNDLE) == []


def test_beaaa17962_rejects_blank_state(tmp_path: Path) -> None:
    data = _bundle_data()
    data["acceptance_rows"][0]["state"] = ""
    errors = validate_bundle(_write_tmp(tmp_path, data))
    assert any("invalid or blank state" in error for error in errors)


def test_beaaa17962_rejects_passed_row_missing_hash(tmp_path: Path) -> None:
    data = _bundle_data()
    row = next(row for row in data["acceptance_rows"] if row["state"] == "passed")
    row["artifacts"][0]["sha256"] = ""
    errors = validate_bundle(_write_tmp(tmp_path, data))
    assert any("invalid artifact sha256" in error for error in errors)


def test_beaaa17962_rejects_aiuc_certification_claim(tmp_path: Path) -> None:
    data = _bundle_data()
    row = next(row for row in data["acceptance_rows"] if row["scope_label"] == "aiuc_1_mapped")
    row["certification_claim"] = True
    row["assessment_basis"] = "AIUC-certified output"
    errors = validate_bundle(_write_tmp(tmp_path, data))
    assert any("must not claim certification" in error for error in errors)
    assert any("forbidden AIUC claim" in error for error in errors)


def test_beaaa17962_rejects_mixed_aiuc_backbond_extension_label(tmp_path: Path) -> None:
    data = _bundle_data()
    row = copy.deepcopy(next(row for row in data["acceptance_rows"] if row["scope_label"] == "backbond_extension"))
    row["row_id"] = "BBX.BAD.MIXED_LABEL"
    row["assessment_basis"] = "AIUC mapped extension subtotal"
    data["acceptance_rows"].append(row)
    errors = validate_bundle(_write_tmp(tmp_path, data))
    assert any("BackBond Extension row" in error for error in errors)


def test_beaaa17962_rejects_production_only_pass(tmp_path: Path) -> None:
    data = _bundle_data()
    row = next(row for row in data["acceptance_rows"] if row["row_id"] == "ROLLBACK.PRODUCTION_ONLY")
    row["state"] = "passed"
    row["commands"] = ["echo fabricated"]
    row["artifacts"] = [{"path": "x", "sha256": "sha256:" + "0" * 64}]
    errors = validate_bundle(_write_tmp(tmp_path, data))
    assert any("ROLLBACK.PRODUCTION_ONLY must be not-evaluated" in error for error in errors)
