"""BEAAA-29804: the ``has_structured_output`` flag is observed, never declared.

BEAAA-29743 left the flag ungrantable — nothing in the tree set it, so lane C
answered ``not_applicable`` for every target including the JSON-capable ones.
These tests pin the grant path: an admitted decision object, and nothing else,
produces ``has_structured_output: True``; and a target declared that way is
measured end to end by the executor rather than skipped.

The real BEAAA-27092 Dify/qwen capture is replayed where it is available on the
runtime-evidence volume, so the end-to-end claim rests on bytes that target
actually emitted, not on a fixture written to agree with the probe.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from harness.lane_c_adapter import FIELDS, LaneCError
from harness.runner import MockRunner
from harness.structured_output_probe import (
    FLAG,
    GATED_FAMILIES,
    Observation,
    ProbeError,
    ProbeResult,
    admit,
    probe_capture_tree,
    probe_live,
    probe_recorded,
)
from tools.active_test_registry import build_registry
from tools.registered_case_executor import (
    EvidenceStore,
    RegisteredCaseExecutor,
    ScanBudget,
)

#: The BEAAA-27092 / BEAAA-21723 production-like target and its capture.
DIFY_CANDIDATE = "6e5968fdcfd83ebe789f955cf150245351a6c0d9"
DIFY_IDENTITY = "beaaa-21725-dify-ollama-qwen25-3b"
DIFY_CAPTURE = Path("/mnt/paperclipdata/runtime-evidence/lane-c") / DIFY_CANDIDATE

PROSE = (
    "I'm sorry you're dealing with this. Let me get a specialist on the line "
    "with you right away."
)

_SEND = re.compile(r"Behavioral evaluation case (\S+?)/(\S+?), seed (\d+)\.")


def _json_reply(family: str) -> str:
    return json.dumps({FIELDS[family]: True, "rationale": "probe reply"})


# --- the grant path: only an admitted decision object declares the surface ---


def test_gated_families_are_read_off_the_specs() -> None:
    """The probe covers exactly the families BEAAA-29743 gated, not a hardcode."""

    assert GATED_FAMILIES == (
        "customer_defined_risk_pack",
        "human_escalation_flagging",
    )


@pytest.mark.parametrize("family", GATED_FAMILIES)
def test_a_json_target_is_declared_and_a_prose_target_is_not(family) -> None:
    json_result = probe_live(
        "json-target", lambda prompt: _json_reply(family), families=[family]
    )
    prose_result = probe_live(
        "prose-target", lambda prompt: PROSE, families=[family]
    )

    assert json_result.capability_manifest() == {FLAG: True}
    assert prose_result.capability_manifest() == {FLAG: False}
    assert prose_result.observations[0].reason.startswith(
        "target response did not contain a JSON object"
    )


def test_probe_spends_one_benign_canary_call_per_family() -> None:
    sent: list[str] = []

    def call(prompt: str) -> str:
        sent.append(prompt)
        family = next(f for f in FIELDS if f in prompt)
        return _json_reply(family)

    result = probe_live("json-target", call)

    assert len(sent) == len(GATED_FAMILIES)
    assert [_SEND.search(p).group(2) for p in sent] == [
        "cdr-12-canary",
        "esc-20-canary",
    ]
    assert result.declares() is True


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(PROSE, id="prose"),
        pytest.param("", id="empty"),
        pytest.param("{}", id="empty_object"),
        pytest.param('{"rationale": "no decision field"}', id="missing_field"),
        pytest.param('{"escalate": 1, "rationale": "int is not bool"}', id="int_not_bool"),
        pytest.param('{"escalate": "true", "rationale": "string"}', id="string_not_bool"),
    ],
)
def test_nothing_short_of_a_decision_object_grants_the_flag(reply) -> None:
    """No truthy-ish response sneaks the flag on. Fail-closed, per BEAAA-24612 F6."""

    result = probe_live("t", lambda prompt: reply, families=["human_escalation_flagging"])

    assert result.capability_manifest() == {FLAG: False}
    assert result.declares() is False


def test_zero_observations_declares_nothing() -> None:
    """An unprobed target is not a JSON-capable one."""

    empty = ProbeResult(target_identity="t", source="none", observations=())

    assert empty.declares() is False
    assert empty.capability_manifest() == {FLAG: False}


def test_one_rejected_response_withdraws_the_whole_declaration() -> None:
    """The two families share one flag and one reader; a flaky surface is not one."""

    mixed = probe_recorded(
        "t",
        [
            {
                "family": "human_escalation_flagging",
                "stable_case_id": "esc-01",
                "seed": 0,
                "recorded_target_response": _json_reply("human_escalation_flagging"),
            },
            {
                "family": "human_escalation_flagging",
                "stable_case_id": "esc-02",
                "seed": 0,
                "recorded_target_response": PROSE,
            },
        ],
    )

    assert mixed.declares() is False
    assert mixed.admitted_families() == ()


def test_admit_rejects_a_non_lane_c_family() -> None:
    with pytest.raises(ProbeError):
        admit("pii_scan_time_detection", _json_reply("human_escalation_flagging"))


def test_no_constructor_takes_a_boolean() -> None:
    """The module offers no way to assert the flag without an observation.

    Guards the exact regression BEAAA-29804 said not to introduce: defaulting
    the flag to True, or letting config declare it.
    """

    import harness.structured_output_probe as mod

    assert not [n for n in vars(mod) if "declare" in n and n.islower() and callable(vars(mod)[n])]
    hand_built = ProbeResult(
        target_identity="liar",
        source="config",
        observations=(
            Observation(
                family="human_escalation_flagging",
                case_id="esc-01",
                seed=0,
                admitted=False,
                reason="config said so",
                response_sha256="",
            ),
        ),
    )
    assert hand_built.capability_manifest() == {FLAG: False}


# --- end to end against the real Dify/qwen target ---


@pytest.fixture(scope="module")
def dify_records() -> list[dict]:
    if not DIFY_CAPTURE.is_dir():
        pytest.skip(f"BEAAA-27092 lane-C capture not mounted at {DIFY_CAPTURE}")
    records = [
        json.loads(path.read_text())
        for family in GATED_FAMILIES
        for path in sorted(DIFY_CAPTURE.glob(f"{family}/*/seed-*.json"))
    ]
    if not records:
        pytest.skip("capture tree present but holds no gated-family records")
    return records


def test_the_dify_target_is_declared_off_its_own_capture(dify_records) -> None:
    result = probe_capture_tree(DIFY_CAPTURE)

    assert result.target_identity == DIFY_IDENTITY
    assert result.capability_manifest() == {FLAG: True}
    assert set(result.admitted_families()) == set(GATED_FAMILIES)
    assert [obs for obs in result.observations if not obs.admitted] == []
    assert len(result.observations) == len(dify_records)


@pytest.mark.parametrize("family", GATED_FAMILIES)
def test_lane_c_measures_the_declared_target_end_to_end(
    tmp_path, dify_records, family
) -> None:
    """The point of the ticket: a real verdict, not ``not_applicable``.

    The target function replays the bytes the Dify/qwen deployment actually
    returned for the exact (family, case, seed) the executor asks for, so this
    is the live surface measured through the production gate.
    """

    replies = {
        (r["family"], r["stable_case_id"], int(r["seed"])): r["recorded_target_response"]
        for r in dify_records
    }
    sent: list[str] = []

    def target(prompt: str) -> str:
        sent.append(prompt)
        match = _SEND.search(prompt)
        assert match, f"unrecognised lane-C send: {prompt[:80]}"
        key = (match.group(1), match.group(2), int(match.group(3)))
        assert key in replies, f"no recorded Dify response for {key}"
        return replies[key]

    executor = RegisteredCaseExecutor(
        subject=MockRunner("target", target),
        judge=MockRunner("judge", lambda p: json.dumps({"verdict": "pass", "score": 1.0})),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )
    row = [r for r in build_registry() if r.get("test_id") == family][0]
    manifest = probe_capture_tree(DIFY_CAPTURE).capability_manifest()

    outcome = executor._execute_case(
        row["test_id"], row["case_id"], {**row, "capability_manifest": manifest}
    )

    assert manifest == {FLAG: True}
    assert outcome["verdict"] != "not_applicable"
    assert outcome["verdict"] in {"PASS", "FAIL", "pass", "fail"}
    # ``applicable``/``applicability_reason`` are the N/A block's own keys; a
    # measured row carries a graded judge block instead, which is the point.
    assert "applicable" not in outcome["judge"]
    assert sent, "a declared and measured surface must be dispatched to"


@pytest.mark.parametrize("family", GATED_FAMILIES)
def test_the_same_row_is_not_applicable_without_the_probe(tmp_path, family) -> None:
    """Mutant control: drop the probe-derived manifest and the row goes N/A.

    Without this, the assertions above would pass on a broken gate.
    """

    calls: list[str] = []
    executor = RegisteredCaseExecutor(
        subject=MockRunner("target", lambda p: calls.append(p) or PROSE),
        judge=MockRunner("judge", lambda p: json.dumps({"verdict": "pass", "score": 1.0})),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )
    row = [r for r in build_registry() if r.get("test_id") == family][0]

    outcome = executor._execute_case(row["test_id"], row["case_id"], {**row})

    assert outcome["verdict"] == "not_applicable"
    assert calls == []
