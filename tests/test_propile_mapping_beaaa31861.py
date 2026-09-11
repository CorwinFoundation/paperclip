from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _mapping():
    return yaml.safe_load(
        (ROOT / "rubric/probe_mappings/propile.yaml").read_text(encoding="utf-8")
    )


def test_propile_renders_base_model_provenance_label():
    mapping = _mapping()
    rendered = mapping["provenance"]["label_template"].format(
        base_model="fixture-model"
    )

    assert rendered == "base-model:fixture-model"
    assert rendered.startswith("base-model:")


def test_propile_remains_evidence_only_and_undispatched():
    rows = _mapping()["probes"]
    active_rows = [row for row in rows if not row.get("not_applicable")]

    assert len(active_rows) == 1
    assert active_rows[0]["weight"] == 0.0
    assert active_rows[0]["evidence_only"] is True
    assert active_rows[0]["garak_live_dispatch"] is False
