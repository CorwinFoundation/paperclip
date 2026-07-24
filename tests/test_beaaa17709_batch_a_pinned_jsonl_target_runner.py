from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests._p4_helpers import MockAgentConfig as P4MockAgentConfig
from tests._p4_helpers import MockGroundedAgent
from tests._p5_helpers import MockAgentConfig as P5MockAgentConfig
from tests._p5_helpers import MockInjectableAgent
from tools.batch_a_pinned_jsonl_target_runner import (
    BatchAPinnedJsonlTargetRunner,
    BatchARunError,
    BatchATarget,
    PINS,
    verify_pins,
)


def _target(
    direct_url: str,
    grounded_url: str,
    *,
    call_cap: int = 170,
) -> BatchATarget:
    return BatchATarget(
        direct_refusal_url=direct_url,
        grounded_agent_url=grounded_url,
        identity="configured-development-target@batch-a-fixture",
        call_cap=call_cap,
        spend_cap_usd=0.0,
    )


def test_known_good_executes_all_pinned_jsonl_cases_with_zero_paid_calls(
    tmp_path: Path,
) -> None:
    with MockInjectableAgent(
        P5MockAgentConfig(refusal_default=True, injection_compliance=0.0)
    ) as direct_url, MockGroundedAgent(
        P4MockAgentConfig(factuality_compliance=1.0, citation_compliance=1.0)
    ) as grounded_url:
        runner = BatchAPinnedJsonlTargetRunner(
            _target(direct_url, grounded_url), tmp_path
        )
        summary = runner.run_all()

    assert summary["case_count"] == 34
    assert summary["trial_count"] == 170
    assert summary["target_calls"] == 170
    assert summary["estimated_paid_call_cost_usd"] == 0.0
    assert summary["customer_data"] is False
    assert summary["score_activation"] is False
    assert {row["family_id"] for row in summary["families"]} == {
        "advbench",
        "factscore",
        "halueval",
        "jailbreakbench",
    }
    assert all(row["verdict"] == "pass" for row in summary["families"])
    assert summary["pinned_inputs"] == {
        str(pin.path.relative_to(Path(__file__).resolve().parents[1])): pin.sha256
        for pin in PINS
    }
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", summary["deterministic_digest"])


def test_known_bad_target_discriminates_direct_and_grounded_paths(tmp_path: Path) -> None:
    with MockInjectableAgent(
        P5MockAgentConfig(refusal_default=False, injection_compliance=1.0)
    ) as direct_url, MockGroundedAgent(
        P4MockAgentConfig(factuality_compliance=0.0, citation_compliance=0.0)
    ) as grounded_url:
        runner = BatchAPinnedJsonlTargetRunner(
            _target(direct_url, grounded_url), tmp_path
        )
        with pytest.raises(BatchARunError, match="failed closed"):
            runner.run_all()

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert {row["verdict"] for row in summary["families"]} == {"fail"}
    assert summary["case_count"] == 34
    assert summary["target_calls"] == 170


def test_unavailable_target_fails_closed_with_summary(tmp_path: Path) -> None:
    runner = BatchAPinnedJsonlTargetRunner(
        _target(
            "http://127.0.0.1:9/unavailable",
            "http://127.0.0.1:9/unavailable",
        ),
        tmp_path,
    )
    with pytest.raises(BatchARunError, match="failed closed"):
        runner.run_all()
    assert (tmp_path / "summary.json").exists()


def test_call_cap_fails_before_target_execution(tmp_path: Path) -> None:
    with MockInjectableAgent(
        P5MockAgentConfig(refusal_default=True, injection_compliance=0.0)
    ) as direct_url, MockGroundedAgent() as grounded_url:
        runner = BatchAPinnedJsonlTargetRunner(
            _target(direct_url, grounded_url, call_cap=39), tmp_path
        )
        with pytest.raises(BatchARunError, match="call cap"):
            runner.run_all()


def test_pin_validation_fails_closed_on_hash_mismatch(tmp_path: Path) -> None:
    pinned = tmp_path / "fixture.jsonl"
    pinned.write_text("# probe-set: fake\n{}\n", encoding="utf-8")
    bad_pin = PINS[0].__class__(
        pinned,
        "0" * 64,
        "advbench",
        "advbench",
        "5f3e7af52ed9da50f5e5d735e0b08f1f3a96d80f",
        "p5_direct_advbench_v1",
        "p5_direct_v1",
        "P3.1",
        "direct_refusal",
    )

    with pytest.raises(BatchARunError, match="sha256 mismatch"):
        verify_pins((bad_pin,))
