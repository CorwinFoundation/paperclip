from pathlib import Path

import yaml


WORKFLOW = Path(__file__).parents[1] / ".github/workflows/scanner-edge-image.yml"


def test_manual_release_sequence_preserves_immutable_rebuild_slots() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    dispatch = workflow[True]["workflow_dispatch"]
    options = dispatch["inputs"]["sequence"]["options"]

    assert options == ["1", "2", "3", "4", "5", "6"]
