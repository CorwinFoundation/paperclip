"""BEAAA-31910: the GOAT attacker is pinned and the pin reaches the manifest.

Condition 1 of the BEAAA-31018 ruling. Without the manifest field, drift caused
by the attacker is unattributable, so the field's absence must fail a test.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from api import full_scan_executor
from api.attacker_pins import (
    ATTACKER_PINS,
    attacker_pin,
    attacker_pins_manifest_block,
    bind_attacker_credentials,
)

DATED_SNAPSHOT = re.compile(r"\d{4}-\d{2}-\d{2}$")


def test_goat_attacker_is_a_dated_snapshot():
    pin = attacker_pin("goat")
    assert pin is not None
    assert DATED_SNAPSHOT.search(pin["red_team_model_name"])


def test_attacker_name_never_starts_with_o():
    # garak/generators/openai.py rejects leading-"o" names as o-class models.
    for pin in ATTACKER_PINS.values():
        if pin["red_team_model_type"].startswith("openai."):
            assert not pin["red_team_model_name"].startswith("o")


def test_determinism_is_not_described_as_the_huggingface_switch():
    for pin in ATTACKER_PINS.values():
        assert "do_sample" not in pin["determinism"]
        assert "best-effort" in pin["determinism"]


def test_unpinned_probe_has_no_attacker():
    assert attacker_pin("dan") is None


def test_credential_bind_prefers_openrouter_over_the_empty_openai_key():
    env = {"OPENAI_API_KEY": "", "OPENROUTER_API_KEY": "sk-or-test"}
    bound = bind_attacker_credentials(env)
    assert bound["OPENAI_API_KEY"] == "sk-or-test"
    assert bound["OPENAI_BASE_URL"].endswith("openrouter.ai/api/v1")


def test_credential_bind_does_not_overwrite_a_real_openai_key():
    env = {"OPENAI_API_KEY": "sk-real", "OPENROUTER_API_KEY": "sk-or-test"}
    assert bind_attacker_credentials(env)["OPENAI_API_KEY"] == "sk-real"


def _bare_job():
    return SimpleNamespace(
        job_id="job-31910",
        agent_id="agent-31910",
        scanner_version="v0",
        corpus_version="v0",
        model_versions={"primary": "fixture", "judges": []},
        seed=0,
        attempts=[],
        registry_cases=[],
        evidence_rows=[],
        approved_blocker_ref=None,
        wall_clock_budget_seconds=None,
        rate_limit_per_minute=None,
    )


def test_manifest_carries_the_attacker_pin_next_to_model_versions():
    manifest = full_scan_executor.build_attempt_manifest(_bare_job())
    keys = list(manifest)
    assert "attacker_pins" in keys, "manifest lost the attacker pin"
    assert keys.index("attacker_pins") == keys.index("model_versions") + 1
    assert manifest["attacker_pins"]["goat"]["attacker_name_resolved"] == (
        ATTACKER_PINS["goat"]["red_team_model_name"]
    )


def test_manifest_block_matches_the_pin_table():
    block = attacker_pins_manifest_block()
    assert set(block) == set(ATTACKER_PINS)
    for probe, entry in block.items():
        assert entry["generator_class"] == ATTACKER_PINS[probe]["red_team_model_type"]
