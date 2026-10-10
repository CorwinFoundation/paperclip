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
    monkeypatch.setattr(importer, "_pin_sampling", lambda seed, cap: None)
    manifest = importer.import_prompts()
    assert manifest["framework_version"] == "0.16.0"
    assert manifest["unique_prompt_count"] == 2
    assert manifest["model_calls"] == 0
    assert manifest["report_only"] is True
    assert len({row["prompt_sha256"] for row in manifest["prompts"]}) == 2


def test_import_pins_garak_prompt_sampling_so_the_bounded_set_is_reproducible(monkeypatch):
    """BEAAA-40048: unseeded garak sampling gave 247/249/250 unique prompts across runs."""
    import random

    monkeypatch.setattr(importlib.metadata, "version", lambda _: "0.16.0")
    pinned = []

    def pin(seed, cap):
        pinned.append((seed, cap))
        random.seed(seed)

    class Probe:
        def __init__(self):
            self.prompts = random.sample([f"prompt-{i}" for i in range(500)], 64)

    monkeypatch.setattr(importer, "_probe_rows", lambda _path: [{
        "probe_id": "garak.encoding.Fixture", "family": "encoding", "module": "encoding", "class": "Fixture",
    }])
    monkeypatch.setattr(importer.importlib, "import_module", lambda _: type("M", (), {"Fixture": Probe}))
    monkeypatch.setattr(importer, "_pin_sampling", pin)
    first = importer.import_prompts()
    random.seed(12345)
    second = importer.import_prompts()
    assert pinned == [(importer.PROMPT_SEED, importer.PROMPT_CAP)] * 2
    assert first["prompts"] == second["prompts"]
    assert (first["prompt_seed"], first["prompt_cap"]) == (0, 64)
