"""BEAAA-33101 D1: `_scoring_active` fails closed, and no mapping relies on it.

Decision B (BEAAA-33102) required inverting the undeclared-mapping default from
True to False *without* changing which families are scoring-active. That only
holds while every mapping that should be scored says so explicitly. Three
mappings were missed on the first pass and silently left the scored set
(72 -> 69), so the guard below is the part that keeps this a no-op.
"""

from pathlib import Path

import yaml

from tools.active_test_registry import _scoring_active

MAPPINGS = Path(__file__).resolve().parents[1] / "rubric" / "probe_mappings"


def _load(path):
    return yaml.safe_load(path.read_text()) or {}


def test_undeclared_mapping_is_not_scoring_active():
    """The bare default must be False, not True."""
    assert _scoring_active({}) is False
    assert _scoring_active({"binding": {}}) is False


def test_explicit_declarations_still_win():
    assert _scoring_active({"scoring_active": True}) is True
    assert _scoring_active({"scoring_active": False}) is False
    assert _scoring_active({"binding": {"scoring_active": True}}) is True
    assert _scoring_active({"binding": {"scoring_active": False}}) is False


def test_no_mapping_relies_on_the_default():
    """Every mapping declares scoring_active, so the flip cannot reprice.

    A mapping that omits the declaration reads as not-scored after D1 while it
    read as scored before, which is exactly the silent repricing decision B
    ruled out. Adding a new mapping without a declaration fails here.
    """
    undeclared = []
    for path in sorted(MAPPINGS.glob("*.yaml")):
        data = _load(path)
        binding = data.get("binding")
        binding = binding if isinstance(binding, dict) else {}
        if binding.get("scoring_active") is None and data.get("scoring_active") is None:
            undeclared.append(path.name)
    assert not undeclared, (
        "mappings with no explicit scoring_active declaration "
        f"(they silently leave the scored set): {undeclared}"
    )
