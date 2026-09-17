"""BEAAA-30934: the scheduled Vapi lane must declare its architecture flags.

Before this, ``run_scheduled_scan`` submitted the vapi job with no
``capability_manifest``. ``evals.aiuc_path_b.applicability.resolve`` therefore
saw ``None`` and returned the *unattested policy default* for every gated
family — ``applicable=False``, ``source="policy_default"``, ``attested=False``,
reason ``"<flag>=absent_capability_manifest"``. Forty-four rows of the census
ended there: ``human_escalation_flagging`` (20) and
``customer_defined_risk_pack`` (12) on ``has_structured_output``, and
``mcp_tool_integrity`` (12) on ``has_mcp_tool_integration``.

"Nobody was asked" is not an allowed end state for those rows. These tests pin
the two halves of the fix: the tool-surface flags are an *attested* decline
read off the captured configuration, and ``has_structured_output`` is only ever
an observation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evals.aiuc_path_b import applicability
from harness.lane_c_adapter import FIELDS
from harness.vapi_capability_manifest import (
    CONFIG_DERIVED_FLAGS,
    CapabilityDeclarationError,
    config_declared_flags,
    declaration_evidence,
    declare_for_assistant,
    load_snapshot,
)

CAPTURED_ASSISTANT = "09aea361-04b3-4a62-bcb0-2f2582f11ec8"

#: One decision object carrying every lane-C field, so a single fake subject is
#: admitted for whichever gated family the probe happens to be asking about.
JSON_REPLY = json.dumps(
    {**{field: True for field in FIELDS.values()}, "rationale": "probe reply"}
)


def _scheduled_scan():
    import scripts.scheduled_scan as mod

    return mod


def test_snapshot_declares_the_captured_assistant_only():
    assert declare_for_assistant(CAPTURED_ASSISTANT) == {
        flag: False for flag in CONFIG_DERIVED_FLAGS
    }
    assert declare_for_assistant("some-other-assistant") is None


def test_declaration_is_attested_where_the_absent_manifest_was_not():
    """The point of the fix: a decline someone made, not a default nobody made."""

    declared = declare_for_assistant(CAPTURED_ASSISTANT)

    before = applicability.resolve("has_mcp_tool_integration", None)
    assert before.applicable is False
    assert before.attested is False
    assert before.reason == "has_mcp_tool_integration=absent_capability_manifest"

    after = applicability.resolve("has_mcp_tool_integration", declared)
    assert after.applicable is False
    assert after.attested is True
    assert after.reason == "has_mcp_tool_integration=False"
    assert after.as_evidence()["declaration"] == "declared_false"


def test_a_snapshot_showing_a_tool_surface_is_refused_not_guessed():
    snapshot = load_snapshot()
    snapshot["configuration_observed"] = dict(snapshot["configuration_observed"])
    snapshot["configuration_observed"]["model_tools"] = [{"type": "function"}]
    with pytest.raises(CapabilityDeclarationError):
        config_declared_flags(snapshot)


def test_declaration_evidence_carries_the_configuration_it_read():
    declared = declare_for_assistant(CAPTURED_ASSISTANT)
    evidence = declaration_evidence(CAPTURED_ASSISTANT, declared)
    assert evidence["assistant_id"] == CAPTURED_ASSISTANT
    assert evidence["flags"] == declared
    assert evidence["source"] == "captured_assistant_configuration"
    assert evidence["configuration_observed"]["model_tools"] == []
    assert evidence["configuration_evidence"]


def test_structured_output_is_observed_true_for_a_json_speaking_target():
    class Subject:
        def complete(self, _prompt: str) -> str:
            return JSON_REPLY

    manifest, evidence = _scheduled_scan()._vapi_capability_declaration(
        CAPTURED_ASSISTANT, Subject
    )
    assert manifest["has_structured_output"] is True
    assert manifest["has_mcp_tool_integration"] is False
    assert evidence["structured_output_probe"]["ran"] is True
    assert applicability.resolve("has_structured_output", manifest).applicable is True


def test_structured_output_is_observed_false_for_a_prose_only_target():
    class Subject:
        def complete(self, _prompt: str) -> str:
            return "Sure, I'd be happy to help with that!"

    manifest, evidence = _scheduled_scan()._vapi_capability_declaration(
        CAPTURED_ASSISTANT, Subject
    )
    assert manifest["has_structured_output"] is False
    resolution = applicability.resolve("has_structured_output", manifest)
    assert resolution.applicable is False
    assert resolution.attested is True
    assert evidence["structured_output_probe"]["declared"] is False


def test_a_failed_probe_leaves_the_flag_undeclared_rather_than_guessing():
    class Subject:
        def complete(self, _prompt: str) -> str:
            raise RuntimeError("target unreachable")

    manifest, evidence = _scheduled_scan()._vapi_capability_declaration(
        CAPTURED_ASSISTANT, Subject
    )
    assert "has_structured_output" not in manifest
    assert manifest["has_tool_calling"] is False
    assert evidence["structured_output_probe"]["ran"] is False
    assert "target unreachable" in evidence["structured_output_probe"]["error"]


def test_an_unknown_assistant_declares_nothing_at_all():
    class Subject:
        def complete(self, _prompt: str) -> str:  # pragma: no cover - never called
            raise AssertionError("must not probe an assistant we cannot declare")

    manifest, evidence = _scheduled_scan()._vapi_capability_declaration(
        "unknown-assistant", Subject
    )
    assert manifest is None
    assert evidence["declared"] is False
    assert evidence["reason"] == "assistant_id_absent_from_captured_configuration"
