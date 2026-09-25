"""BEAAA-35637: missing release evidence must fail closed, not silently empty.

Two guards. The first is the bug itself: a ledger read that returned ``{}`` when
its file was absent, which is how twelve ``tool_misuse_param_tamper`` rows
reached the image with no execution evidence on BEAAA-35634. The second is the
class of bug: ``.dockerignore`` denies ``artifacts/*`` and re-includes evidence
files one at a time, and until now nothing connected that list to the code that
reads them.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import image_artifact_allowlist as allowlist  # noqa: E402
from tools.active_test_registry import (  # noqa: E402
    MissingReleaseEvidenceError,
    _beaaa31092_dev_scored_rows,
    _beaaa35380_dev_scored_rows,
    _load_evidence_json,
)


def test_missing_required_evidence_raises(tmp_path: Path) -> None:
    with pytest.raises(MissingReleaseEvidenceError) as excinfo:
        _load_evidence_json(tmp_path / "absent.json", context="unit")
    message = str(excinfo.value)
    assert "absent.json" in message
    assert ".dockerignore" in message, "the error must name where to look"


def test_malformed_required_evidence_raises(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(MissingReleaseEvidenceError):
        _load_evidence_json(broken, context="unit")


def test_optional_evidence_returns_none_and_logs(tmp_path: Path, caplog) -> None:
    with caplog.at_level("WARNING"):
        assert _load_evidence_json(tmp_path / "absent.json", required=False) is None
    assert any("optional evidence file" in record.message for record in caplog.records), (
        "an optional skip must be logged; silence is the defect this ticket fixes"
    )


def test_valid_evidence_round_trips(tmp_path: Path) -> None:
    payload = tmp_path / "ledger.json"
    payload.write_text(json.dumps({"rows": []}), encoding="utf-8")
    assert _load_evidence_json(payload) == {"rows": []}


@pytest.mark.parametrize(
    "reader",
    (_beaaa31092_dev_scored_rows, _beaaa35380_dev_scored_rows),
    ids=("beaaa31092", "beaaa35380"),
)
def test_ledger_readers_carry_rows(reader) -> None:
    """Both ledgers are present in the repo tree and resolve scored rows."""
    reader.cache_clear()
    rows = reader()
    assert rows, "ledger read returned nothing; an empty result is now never silent"


@pytest.mark.parametrize(
    "path",
    sorted(
        path
        for path, classification in allowlist.RUNTIME_ARTIFACT_INVENTORY.items()
        if classification == "read_required"
    ),
)
def test_required_runtime_artifacts_ship_in_the_image(path: str) -> None:
    assert allowlist.image_includes(path), (
        f"{path} is read at runtime but .dockerignore keeps it out of the image"
    )


def test_required_runtime_artifacts_exist_in_the_repo() -> None:
    missing = [
        path
        for path, classification in allowlist.RUNTIME_ARTIFACT_INVENTORY.items()
        if classification == "read_required" and not (REPO_ROOT / path).exists()
    ]
    assert not missing, f"declared runtime evidence absent from the tree: {missing}"


def test_no_undeclared_runtime_artifact_paths() -> None:
    """The durable half: a new artifacts/ path fails here, not at the release gate."""
    found = allowlist.scan_runtime_artifact_literals()
    undeclared = sorted(set(found) - set(allowlist.RUNTIME_ARTIFACT_INVENTORY))
    detail = {path: sorted(found[path]) for path in undeclared}
    assert not undeclared, (
        "these artifacts/ paths are named by the live runtime closure but are not "
        f"classified in RUNTIME_ARTIFACT_INVENTORY: {detail}"
    )


def test_inventory_classifications_are_valid() -> None:
    invalid = {
        path: classification
        for path, classification in allowlist.RUNTIME_ARTIFACT_INVENTORY.items()
        if classification not in allowlist.VALID_CLASSIFICATIONS
    }
    assert not invalid, invalid


def test_audit_is_clean_on_this_tree() -> None:
    assert allowlist.audit() == []


def test_dockerignore_parser_strips_negation_and_trailing_slash() -> None:
    parsed = allowlist._dockerignore_patterns("# c\n\nartifacts/*\n!artifacts/keep/\n")
    assert parsed == [("artifacts/*", False), ("artifacts/keep", True)]


def test_dockerignore_matcher_respects_directory_boundaries() -> None:
    # Parsed form: ``!`` and the trailing ``/`` are stripped by the parser.
    patterns = [("artifacts/*", False), ("artifacts/keep", True), ("artifacts/keep/*", False)]
    assert not allowlist.image_includes("artifacts/other/file.json", patterns)
    assert not allowlist.image_includes("artifacts/keep/file.json", patterns)
    assert allowlist.image_includes("artifacts/keep", patterns)
