from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from tools import probe_registry


REPO = Path(__file__).resolve().parents[1]


def _registry() -> dict:
    return probe_registry.build_registry(REPO)


def test_schema_and_sha_binding() -> None:
    registry = _registry()
    schema = json.loads((REPO / probe_registry.SCHEMA_PATH).read_text())
    jsonschema.validate(registry, schema)
    assert registry["metadata"]["source_closure_sha"] == probe_registry._source_sha(REPO)
    assert registry["metadata"]["generated_from_commit"] == subprocess.check_output(
        ["git", "rev-parse", "HEAD^"], cwd=REPO, text=True
    ).strip()
    assert "repo_sha" not in registry["metadata"]


def test_stable_ids_are_unique_and_category_accounting_is_exact() -> None:
    registry = _registry()
    ids = [row["stable_id"] for row in registry["rows"]]
    assert len(ids) == len(set(ids))
    for category, count in registry["category_counts"].items():
        assert count == sum(row["categories"][category] for row in registry["rows"])
    assert registry["category_counts"]["registered"] == len(registry["rows"])


def test_duplicate_stable_id_inputs_fail_closed() -> None:
    row = {
        "framework": "duplicate-fixture",
        "corpus_or_family": "duplicate-fixture",
        "case_id_or_probe_id": "same-case",
        "rubric_point_id": "P1.1",
        "mode": "fixture",
        "source": "first.json",
    }
    duplicate = {**row, "source": "second.json"}
    with pytest.raises(ValueError, match="duplicate authoritative probe stable ID"):
        probe_registry._index_unique_rows([row, duplicate])


def test_legacy_2202_is_not_default_executed_reported_or_scored() -> None:
    counts = _registry()["category_counts"]
    assert counts["executable"] == 2202
    assert counts["executable_on_default_runtime"] < 2202
    assert counts["executed_in_latest_evidenced_run"] < 2202
    assert counts["reported"] < 2202
    assert counts["scoring_active"] < 2202


def test_check_mode_fails_closed_on_drift(tmp_path: Path) -> None:
    output = tmp_path / "registry.json"
    command = [sys.executable, "-m", "tools.probe_registry", "--repo", str(REPO), "--emit", str(output)]
    subprocess.check_call(command, cwd=REPO)
    subprocess.check_call(command + ["--check"], cwd=REPO)
    output.write_text("{}\n")
    assert subprocess.run(command + ["--check"], cwd=REPO).returncode == 1
