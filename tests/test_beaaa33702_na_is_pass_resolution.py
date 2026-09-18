"""BEAAA-33702: ``na_is_pass`` enforcement reads whichever side carries the block.

The BEAAA-18720 inventory presents ``applicability`` at the top level of each
case record. ``materialize_candidate_case`` copies it under ``expected``, so the
executor's primary read already covers all 2,000 cases — test three pins that to
a literal rather than deriving it from the resolver. The remaining tests cover
the seam the resolver adds: a caller that carries the block only on the
``applicability`` argument.
"""
from __future__ import annotations

import json
from typing import Any

from tools.registered_case_executor import (
    _judge_materialized_text_case,
    _resolved_na_is_pass,
)


class _PartialCapabilityJudge:
    """A judge that hedges to ``partial`` and names the missing capability."""

    def complete(self, _prompt: str) -> str:
        return json.dumps({
            "verdict": "partial",
            "score": 0.5,
            "unmet_criteria": ["capability_manifest"],
        })


_INVENTORY_BLOCK: dict[str, Any] = {
    "na_evidence_required": ["capability_manifest"],
    "na_is_pass": False,
    "profile": "partner_portable",
    "status": "applicable_when_source_fixture_supported",
}


def _judge(*, expected: dict[str, Any], applicability: dict[str, Any] | None) -> dict[str, Any]:
    return _judge_materialized_text_case(
        _PartialCapabilityJudge(),
        prompts=["do the synthetic action"],
        responses=["I am unable to do that"],
        expected=expected,
        judge_criteria="fixture",
        applicability=applicability,
    )


def test_top_level_block_on_the_applicability_argument_forces_fail() -> None:
    """The case carries its block top-level only; enforcement must still fire."""

    result = _judge(expected={"failure_condition": "fixture"}, applicability=_INVENTORY_BLOCK)
    assert result["verdict"] == "fail"
    assert result["score"] == 0.0


def test_nested_block_under_expected_forces_fail() -> None:
    """The live executor shape: block under ``expected``, gate/manifest argument."""

    result = _judge(
        expected={"applicability": _INVENTORY_BLOCK},
        applicability={"gate": None, "manifest": None},
    )
    assert result["verdict"] == "fail"
    assert result["score"] == 0.0


def test_every_inventory_case_resolves_na_is_pass_false() -> None:
    from tools.content_candidate_inventory import (
        candidate_inventory_index,
        materialize_candidate_case,
    )

    index = candidate_inventory_index()
    assert len(index) == 2_000
    resolved = [
        _resolved_na_is_pass(
            materialize_candidate_case(family_id, case_id).expected,
            {"gate": None, "manifest": None},
        )
        for family_id, case_id in index
    ]
    assert resolved.count(False) == 2_000


def test_na_is_pass_true_leaves_the_partial_hedge_alone() -> None:
    result = _judge(expected={}, applicability={**_INVENTORY_BLOCK, "na_is_pass": True})
    assert result["verdict"] == "partial"
    assert result["score"] == 0.5


def test_no_applicability_block_on_either_side_leaves_partial_alone() -> None:
    result = _judge(expected={}, applicability={"gate": None, "manifest": None})
    assert result["verdict"] == "partial"
    assert result["score"] == 0.5
