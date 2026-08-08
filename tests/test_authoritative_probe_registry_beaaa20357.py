from __future__ import annotations

import json
import os
import subprocess
import sys
from functools import cache
from pathlib import Path

import jsonschema
import pytest

from tools import probe_registry


REPO = Path(__file__).resolve().parents[1]


@cache
def _registry() -> dict:
    return probe_registry.build_registry(REPO)


def test_schema_and_sha_binding() -> None:
    registry = _registry()
    schema = json.loads((REPO / probe_registry.SCHEMA_PATH).read_text())
    jsonschema.validate(registry, schema)
    assert registry["metadata"]["source_closure_sha"] == probe_registry._source_sha(REPO)
    assert registry["metadata"]["generated_from_content_sha"] == probe_registry._source_sha(REPO)
    assert registry["metadata"]["generated_from_content_sha_kind"] == "sha1-authoritative-source-closure-v1"
    assert "generated_from_commit" not in registry["metadata"]
    assert "repo_sha" not in registry["metadata"]


def test_release_epoch_semantics_match_probe_set_version() -> None:
    metadata = _registry()["metadata"]
    assert metadata["probe_set_version"] in metadata["generation_timestamp_semantics"]


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


def test_content_provenance_is_independent_of_merge_topology(tmp_path: Path) -> None:
    """Identical authoritative inputs bind identically on merge-shaped history."""
    source = probe_registry._source_sha(REPO)
    candidate = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    clone = tmp_path / "repo"
    subprocess.check_call(["git", "clone", "--quiet", "--no-hardlinks", str(REPO), str(clone)])
    subprocess.check_call(["git", "config", "user.email", "registry-test@backbond.invalid"], cwd=clone)
    subprocess.check_call(["git", "config", "user.name", "Registry Test"], cwd=clone)
    subprocess.check_call(["git", "checkout", "-q", "-b", "integration-base", candidate], cwd=clone)
    subprocess.check_call(["git", "checkout", "-q", "-b", "side"], cwd=clone)
    (clone / "topology-only.txt").write_text("side\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "topology-only.txt"], cwd=clone)
    subprocess.check_call(["git", "commit", "-qm", "topology-only side commit"], cwd=clone)
    subprocess.check_call(["git", "checkout", "-q", "integration-base"], cwd=clone)
    (clone / "topology-main.txt").write_text("main\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "topology-main.txt"], cwd=clone)
    subprocess.check_call(["git", "commit", "-qm", "topology-only main commit"], cwd=clone)
    subprocess.check_call(["git", "merge", "--no-ff", "-qm", "prospective merge", "side"], cwd=clone)

    merged = probe_registry.build_registry(clone)
    assert merged["metadata"]["generated_from_content_sha"] == source
    assert merged["metadata"]["source_closure_sha"] == source


def test_failed_integration_gate_makes_push_unreachable(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    push_script = scripts / "push_authoritative_probe_registry_candidate.sh"
    push_script.write_bytes((REPO / "scripts" / push_script.name).read_bytes())
    push_script.chmod(0o755)
    gate = scripts / "authoritative_probe_registry_integration_gate.sh"
    gate.write_text("#!/bin/sh\nexit 23\n", encoding="utf-8")
    gate.chmod(0o755)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    push_marker = tmp_path / "push-called"
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        "  *'rev-parse HEAD'*) echo candidate-sha ;;\n"
        f"  *' push '*) touch {push_marker} ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    env = {"PATH": f"{fake_bin}:{os.environ['PATH']}"}

    result = subprocess.run([str(push_script), "candidate-sha"], cwd=tmp_path, env=env)
    assert result.returncode == 23
    assert not push_marker.exists()
