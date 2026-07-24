from __future__ import annotations

import hashlib
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
    contract = (ROOT / "contracts" / "are-scanner-api-v1.md").read_text(encoding="utf-8")
    contract_version = re.search(r'^version: "(v[^"]+)"$', contract, flags=re.MULTILINE)

    assert SCANNER_VERSION == manifest["scanner_version"]
    assert CONTRACT_VERSION == manifest["contract_version"]
    assert rubric["version"] == manifest["rubric_version"]
    assert contract_version is not None
    assert f"are-scanner-api-v1@{contract_version.group(1)}" == manifest["contract_version"]

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
        assert hashlib.sha256(payload).hexdigest() == lock["sha256"]
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
            rf"^{re.escape(distribution)}=={re.escape(expected)}$",
            requirement,
            flags=re.MULTILINE,
        )
