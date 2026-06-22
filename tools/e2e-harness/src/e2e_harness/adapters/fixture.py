"""Fixture (replay) capture adapter (BEAAA-7427).

The "vendor" here is our own adversarial scenario kit. This adapter lets the
full chain — capture -> ARE scan + score -> Monitor claim path — be
unit-exercised end to end with zero live dependencies, so the live run is the
*only* thing waiting on the Founder's vendor pick. It is also the reference
implementation the three vendor adapters target.
"""
from __future__ import annotations

from typing import Any

from .base import CaptureAdapter, CapturedSession
from ..scenario_kit import AdversarialScenario, Turn


class FixtureAdapter(CaptureAdapter):
    vendor = "fixture"
    export_api_doc = "in-repo adversarial scenario kit (e2e_harness.scenario_kit)"

    def to_session(self, raw_export: dict[str, Any]) -> CapturedSession:
        scenario = raw_export.get("scenario")
        if not isinstance(scenario, AdversarialScenario):
            raise TypeError("fixture adapter expects {'scenario': AdversarialScenario}")
        return self.session_from_scenario(scenario)

    def session_from_scenario(self, scenario: AdversarialScenario) -> CapturedSession:
        return CapturedSession(
            session_id=scenario.scenario_id,
            agent_id="e2e-test-target-agent",
            vendor=self.vendor,
            turns=tuple(scenario.conversation),
            metadata={
                "scenario_id": scenario.scenario_id,
                "peril": scenario.peril,
                "attack_family": scenario.attack_family,
            },
        )

    def fetch(self, credentials: dict[str, Any], **params: Any) -> list[dict[str, Any]]:
        # No credentials needed: the fixture adapter replays the scenario kit.
        from ..scenario_kit import SCENARIOS

        return [{"scenario": s} for s in SCENARIOS]
