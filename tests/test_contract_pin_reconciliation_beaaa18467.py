from __future__ import annotations

import json
import tomllib
from pathlib import Path

import yaml

from scanners import CONTRACT_VERSION


REPO = Path(__file__).resolve().parents[1]
EXPECTED_CONTRACT = "are-scanner-api-v1@v1.3.0"


def test_active_release_surfaces_share_v1_3_0_contract_pin() -> None:
    contract = (REPO / "contracts" / "are-scanner-api-v1.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(contract.split("---", 2)[1])
    release = json.loads(
        (REPO / "release" / "backbond-scanner-release.json").read_text(encoding="utf-8")
    )
    metadata = tomllib.loads(
        (REPO / "scanner-os" / "release-metadata.toml").read_text(encoding="utf-8")
    )

    assert f"are-scanner-api-v1@{frontmatter['version']}" == EXPECTED_CONTRACT
    assert CONTRACT_VERSION == EXPECTED_CONTRACT
    assert release["contract_version"] == EXPECTED_CONTRACT
    assert metadata["scanner"]["contract"] == EXPECTED_CONTRACT


def test_active_golden_fixtures_and_n5_report_share_v1_3_0_contract_pin() -> None:
    fixture_paths = sorted(
        (REPO / "tests" / "fixtures" / "golden_agents").glob("*/expected_score.json")
    )
    assert fixture_paths
    for path in fixture_paths:
        fixture = json.loads(path.read_text(encoding="utf-8"))
        assert fixture["contract_version"] == EXPECTED_CONTRACT, path

    gate_source = (REPO / "scripts" / "release_gate_n5.py").read_text(encoding="utf-8")
    assert f'"contract": "{EXPECTED_CONTRACT}"' in gate_source
    assert f'contracts_referenced: ["{EXPECTED_CONTRACT}"' in gate_source
