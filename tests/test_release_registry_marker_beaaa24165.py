from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.active_test_registry import build_registry
from tools.release_registry_marker import (
    _normalized_registry_bytes,
    build_release_registry_marker,
)


REPO = Path(__file__).resolve().parents[1]
EXPECTED_NORMALIZED_REGISTRY_SHA256 = (
    "4b86b69ba47af8ee242ee0825fc994703e4407de352efa5068bfde989d114485"
)


def test_marker_derives_counts_and_hash_from_same_generated_registry() -> None:
    rows = build_registry(REPO / "rubric" / "probe_mappings")
    marker = build_release_registry_marker(REPO)

    assert marker["published_executable_count"] == 2_212
    assert marker["generated_executable_count"] == 2_212
    assert marker["distinct_runtime_count"] == 2_211
    assert marker["normalized_registry_sha256"] == hashlib.sha256(
        _normalized_registry_bytes(rows)
    ).hexdigest()
    assert marker["normalized_registry_sha256"] == EXPECTED_NORMALIZED_REGISTRY_SHA256
    assert len(marker["excluded_rows"]) == 40
    assert {row["disposition"] for row in marker["excluded_rows"]} == {
        "incompatible",
        "reported_not_scored",
    }


def test_marker_fails_closed_if_published_count_drifts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tools.release_registry_marker.load_published_count", lambda _path: 2_211)
    with pytest.raises(ValueError, match="generated executable count does not match"):
        build_release_registry_marker(REPO)


def test_rendered_manifest_embeds_exact_marker(tmp_path: Path) -> None:
    output = tmp_path / "release.json"
    from scripts.render_release_manifest import render

    render(
        REPO / "release" / "backbond-scanner-release.json",
        output,
        "a" * 40,
    )
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["build_sha"] == "a" * 40
    assert manifest["scanner_version"] == "0.5.0"
    assert manifest["corpus_version"] == "0.2.0"
    assert manifest["registry_marker"] == build_release_registry_marker(REPO)
