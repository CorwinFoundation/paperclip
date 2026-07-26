from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from scanners import CONTRACT_VERSION, SCANNER_VERSION
from scripts import beaaa8225_consolidated_full_coverage as retell
from scripts import beaaa8389_rasa_consolidated_grade_card as rasa


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "release" / "backbond-scanner-release.json"


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_every_version_surface_resolves_to_release_authority() -> None:
    manifest = _manifest()
    rubric = yaml.safe_load((ROOT / "rubric" / "rubric.yaml").read_text(encoding="utf-8"))
    framework_registry = json.loads(
        (ROOT / manifest["framework_registry"]["path"]).read_text(encoding="utf-8")
    )
    assert SCANNER_VERSION == manifest["scanner_version"]
    assert CONTRACT_VERSION == manifest["contract_version"]
    assert rubric["version"] == manifest["rubric_version"]
    assert manifest["contract_version"] == "are-scanner-api-v1@v1.0"
    assert manifest["release_gate_corpus_version"] == "2026.6.24"
    assert manifest["python_version"] == "3.11"
    assert (
        framework_registry["registry_version"]
        == manifest["framework_registry"]["registry_version"]
    )
    assert (
        framework_registry["frameworks"][0]["mapping_version"]
        == manifest["framework_registry"]["mapping_version"]
    )
    assert manifest["approved_source_commits"] == {
        "wave_1a_live_lineage": "d6542eb52b1ea436fbd0d48cf7ebf78dae88188e",
        "release_truth": "30de03e90f5377d051cc99a6f348be5057c67639",
        "production_controls": "876a6daf9aefcac94f58657a64f3dd9cd4ce04d5",
        "assessment_envelope": "35419f819737a5e38865a114898ff1590e1419f3",
    }

    assert retell.CORPUS_VERSION == manifest["corpus_profiles"]["retell_grade_card"]["corpus_version"]
    assert retell.PROBE_SET_VERSION == manifest["corpus_profiles"]["retell_grade_card"]["probe_set_version"]
    assert rasa.CORPUS_VERSION == manifest["corpus_profiles"]["rasa_grade_card"]["corpus_version"]
    assert rasa.PROBE_SET_VERSION == manifest["corpus_profiles"]["rasa_grade_card"]["probe_set_version"]

    fixture_versions: dict[str, str] = {}
    for fixture in sorted((ROOT / "tests" / "fixtures" / "golden_agents").glob("*/expected_score.json")):
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        if not payload:
            continue
        assert payload["scanner_version"] == manifest["scanner_version"]
        assert payload["rubric_version"] == manifest["rubric_version"]
        assert payload["contract_version"] == manifest["contract_version"]
        fixture_versions[fixture.parent.name] = payload.get("corpus_version", "")
    assert fixture_versions == manifest["corpus_profiles"]["golden_fixtures"]


def test_hash_locked_environments_match_release_authority() -> None:
    manifest = _manifest()
    for lock in manifest["locks"].values():
        path = ROOT / lock["path"]
        payload = path.read_bytes()
        text = payload.decode("utf-8")
        assert "--hash=sha256:" in text

    framework_requirements = {
        "garak": ROOT / "requirements-eval-garak.txt",
        "agentdojo": ROOT / "requirements-eval-agentdojo.txt",
        "pyrit": ROOT / "requirements-eval-pyrit.txt",
        "inspect-ai": ROOT / "requirements-eval-inspect.txt",
    }
    for distribution, path in framework_requirements.items():
        expected = manifest["framework_versions"][distribution]
        requirement = path.read_text(encoding="utf-8")
        assert re.search(
            rf"^{re.escape(distribution)}=={re.escape(expected)}\s*\\?$",
            requirement,
            flags=re.MULTILINE,
        )
