"""BEAAA-31938: atkgen.Tox attacker is pinned to the cached HF revision.

Condition 1 of the BEAAA-31018 ruling applied to the atkgen family, which
carries weight 1.0 and feeds are_total today. Unlike GOAT, this attacker is a
local HuggingFace pipeline (gpt2-sized), so determinism uses hf_args.do_sample
rather than temperature, and no API redirect is needed.
"""

from __future__ import annotations

import re

from api.attacker_pins import ATTACKER_PINS, attacker_pin, attacker_pins_manifest_block

HF_SHA = re.compile(r"^[0-9a-f]{40}$")


def test_atkgen_attacker_is_pinned():
    pin = attacker_pin("atkgen.Tox")
    assert pin is not None, "atkgen probe has no attacker pin"


def test_atkgen_pin_revision_is_40char_sha():
    pin = attacker_pin("atkgen")
    assert pin is not None
    revision = pin["red_team_model_config"]["hf_args"]["revision"]
    assert HF_SHA.match(revision), f"revision is not a 40-char sha: {revision!r}"


def test_atkgen_pin_uses_hf_pipeline_not_openai():
    pin = ATTACKER_PINS["atkgen"]
    assert pin["red_team_model_type"] == "huggingface.Pipeline"


def test_atkgen_determinism_uses_do_sample_not_temperature():
    pin = ATTACKER_PINS["atkgen"]
    hf_args = pin["red_team_model_config"]["hf_args"]
    assert hf_args.get("do_sample") is False, "atkgen must disable sampling for determinism"
    assert "temperature" not in hf_args, "temperature is ignored when do_sample=False; remove it"


def test_atkgen_pin_model_name_is_correct():
    pin = ATTACKER_PINS["atkgen"]
    assert pin["red_team_model_name"] == "garak-llm/attackgeneration-toxicity_gpt2"


def test_manifest_surfaces_the_atkgen_revision():
    """The name is a moving branch; only the revision attributes drift."""
    entry = attacker_pins_manifest_block()["atkgen"]
    assert HF_SHA.match(entry["attacker_revision"]), entry
    assert entry["attacker_revision"] == (
        ATTACKER_PINS["atkgen"]["red_team_model_config"]["hf_args"]["revision"]
    )
