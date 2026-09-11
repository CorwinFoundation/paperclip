from pathlib import Path

import importlib.util
import pytest

from tools.validate_garak_registry import validate


ROOT = Path(__file__).resolve().parents[1]


def test_garak_0160_registry_resolves_or_retire_reasoned_rows():
    if importlib.util.find_spec("garak") is None and not any(
        (Path("/opt/are-scanner/eval-venvs/garak-venv") / "lib").glob(
            "python*/site-packages/garak"
        )
    ):
        pytest.skip("garak is verified in the dedicated pinned eval-venv CI job")
    assert validate(ROOT / "rubric/probe_mappings/garak_beaaa152.yaml") == []


def test_propile_probe_ids_resolve_in_pinned_garak():
    if importlib.util.find_spec("garak") is None and not any(
        (Path("/opt/are-scanner/eval-venvs/garak-venv") / "lib").glob(
            "python*/site-packages/garak"
        )
    ):
        pytest.skip("garak is verified in the dedicated pinned eval-venv CI job")
    assert validate(ROOT / "rubric/probe_mappings/propile.yaml") == []
