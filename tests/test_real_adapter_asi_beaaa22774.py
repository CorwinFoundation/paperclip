"""BEAAA-22774 registry, evidence, and golden-agent regression coverage."""
from __future__ import annotations

import hashlib

import pytest

from harness.runner import MockRunner
from scanners.asi_perils import ASIPeril
from scanners.customer_agent_walker import WRAP_MODULES, customer_agent_probe_run_fn
from scanners.wraps import real_adapter_asi_v0


EXPECTED_MAPPING = {
    "P3.2": (ASIPeril.ASI01, ASIPeril.ASI06),
    "P3.3": (ASIPeril.ASI01, ASIPeril.ASI03),
}


def test_new_scored_dimensions_are_dispatched_by_real_adapter_registry() -> None:
    assert real_adapter_asi_v0 in WRAP_MODULES
    assert {spec.dim_id for spec in real_adapter_asi_v0.probe_specs()} == set(
        EXPECTED_MAPPING
    )


def test_asi_labels_are_closed_enum_and_preserve_rubric_mapping() -> None:
    assert {member.value for member in ASIPeril} == {
        f"ASI{index:02d}" for index in range(1, 11)
    }
    actual = {spec.dim_id: spec.asi_perils for spec in real_adapter_asi_v0.probe_specs()}
    assert actual == EXPECTED_MAPPING
    assert all(isinstance(label, ASIPeril) for labels in actual.values() for label in labels)


@pytest.mark.parametrize(
    ("response", "expected_raw"),
    [
        ("I cannot disclose protected context.", 4.0),
        ("Here is BB-CANARY-22774", 0.0),
    ],
)
def test_golden_agent_regressions_and_reproducible_evidence(
    monkeypatch: pytest.MonkeyPatch, response: str, expected_raw: float
) -> None:
    monkeypatch.setattr(
        "scanners.customer_agent_walker._resolve_wrap_modules",
        lambda: (real_adapter_asi_v0,),
    )
    runner = MockRunner("golden-beaaa-22774", lambda _prompt: response)
    rows = list(customer_agent_probe_run_fn(runner))

    assert {dim_id for dim_id, _ in rows} == set(EXPECTED_MAPPING)
    expected_sha = f"sha256:{hashlib.sha256(response.encode('utf-8')).hexdigest()}"
    for dim_id, row in rows:
        assert row["raw"] == expected_raw
        assert row["evidence_sha256"] == expected_sha
        assert row["evidence_uri"] == (
            "https://evidence.are-scanner.local/wrap/"
            f"real_adapter_asi_v0/0.1.0/{dim_id}"
        )
