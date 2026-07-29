import json
from pathlib import Path

import pytest

from tools.oss_pilot_manifest import ManifestValidationError, validate_manifests


def _entry() -> dict:
    return {
        "source_project": "owner/project",
        "source_commit": "a" * 40,
        "source_path": "cases.json",
        "source_case_id": "case-1",
        "source_integrity": "sha256:" + "b" * 64,
        "license": "MIT; third_party/oss-pilots/project-LICENSE",
        "local_case_id": "oss.project.case-1",
        "capability_family": "example",
        "case_class": "agent_behavior",
        "applicability": "predicate_v1",
        "oracle_version": "oracle_v1",
    }


def _write(tmp_path: Path, entries: list[dict], *, enabled: bool = False) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"schema_version": 1, "enabled": enabled, "entries": entries}))
    return path


def test_repository_manifests_are_valid_and_disabled():
    paths = sorted(Path("config/oss-pilots").glob("*.json"))
    assert len(paths) == 6
    assert len(validate_manifests(paths)) == 8


@pytest.mark.parametrize("mutable", ["main", "v1.0.1", "fc0055d"])
def test_rejects_mutable_source_refs(tmp_path: Path, mutable: str):
    entry = _entry()
    entry["source_commit"] = mutable
    with pytest.raises(ManifestValidationError, match="mutable"):
        validate_manifests([_write(tmp_path, [entry])])


def test_rejects_duplicate_local_ids(tmp_path: Path):
    first = _entry()
    second = {**_entry(), "source_case_id": "case-2"}
    with pytest.raises(ManifestValidationError, match="duplicate local_case_id"):
        validate_manifests([_write(tmp_path, [first, second])])


def test_rejects_missing_provenance(tmp_path: Path):
    entry = _entry()
    del entry["source_integrity"]
    with pytest.raises(ManifestValidationError, match="missing provenance"):
        validate_manifests([_write(tmp_path, [entry])])


def test_rejects_unknown_case_class(tmp_path: Path):
    entry = _entry()
    entry["case_class"] = "benchmark"
    with pytest.raises(ManifestValidationError, match="unknown case_class"):
        validate_manifests([_write(tmp_path, [entry])])


def test_rejects_enabled_by_default(tmp_path: Path):
    with pytest.raises(ManifestValidationError, match="default disabled"):
        validate_manifests([_write(tmp_path, [_entry()], enabled=True)])
