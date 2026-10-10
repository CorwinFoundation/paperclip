import importlib.metadata

import pytest

from tools import full_scan_k_garak_import as importer


def test_garak_import_scope_is_closed_to_requested_families():
    rows = importer._probe_rows()
    assert rows
    assert {row["family"] for row in rows} <= {"leakreplay", "encoding", "promptinject"}
    assert all(row["probe_id"].startswith("garak.") for row in rows)


def test_import_rejects_any_version_other_than_pinned(monkeypatch):
    monkeypatch.setattr(importlib.metadata, "version", lambda _: "0.10.0")
    with pytest.raises(RuntimeError, match="version mismatch"):
        importer.import_prompts()


def test_import_deduplicates_prompt_text_and_stamps_report_only(monkeypatch):
    monkeypatch.setattr(importlib.metadata, "version", lambda _: "0.16.0")

    class Probe:
        prompts = ["same", "same", "other"]

    monkeypatch.setattr(importer, "_probe_rows", lambda _path: [{
        "probe_id": "garak.encoding.Fixture", "family": "encoding", "module": "encoding", "class": "Fixture",
    }])
    monkeypatch.setattr(importer.importlib, "import_module", lambda _: type("M", (), {"Fixture": Probe}))
    manifest = importer.import_prompts()
    assert manifest["framework_version"] == "0.16.0"
    assert manifest["unique_prompt_count"] == 2
    assert manifest["model_calls"] == 0
    assert manifest["report_only"] is True
    assert len({row["prompt_sha256"] for row in manifest["prompts"]}) == 2
