from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tools.repin_evaluator import repin


FAMILIES = (
    "tool_misuse_param_tamper",
    "tool_misuse_scope_escape",
    "tool_misuse_unauthorized_call",
)


def _repo(tmp_path: Path, evaluator: bytes = b"merged evaluator\n") -> Path:
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "registered_case_executor.py").write_bytes(evaluator)
    empty_pin = "0" * 64
    entries = "\n".join(
        f'    "{family}": "{empty_pin}",' for family in FAMILIES
    )
    (tools / "tranche1_observational.py").write_text(
        f"EVALUATOR_SHA256 = {{\n{entries}\n}}\n", encoding="utf-8"
    )
    return tmp_path


def test_repin_derives_and_rewrites_all_family_pins(tmp_path):
    repo = _repo(tmp_path)
    expected = hashlib.sha256(b"merged evaluator\n").hexdigest()

    old_pin, new_pin = repin(repo)

    assert old_pin == "0" * 64
    assert new_pin == expected
    assert (repo / "tools" / "tranche1_observational.py").read_text().count(expected) == 3


@pytest.mark.parametrize("conflicted", ["evaluator", "pin"])
def test_repin_refuses_unresolved_conflict_markers(tmp_path, conflicted):
    repo = _repo(tmp_path)
    path = repo / "tools" / (
        "registered_case_executor.py"
        if conflicted == "evaluator"
        else "tranche1_observational.py"
    )
    path.write_text("<<<<<<< ours\n=======\n>>>>>>> theirs\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="unresolved conflict markers"):
        repin(repo)
