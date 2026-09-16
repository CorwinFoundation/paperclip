"""Synthetic model-dependency declaration and fingerprint surface."""
from __future__ import annotations

import hashlib
from typing import Any, Mapping

from harness.mock_tools.base import Operation, SandboxTool
from harness.mock_tools.interface import Impact


class ModelDependencyTool(SandboxTool):
    surface = "model_dependency"
    operations = {
        "declare_model_dependency": Operation("declare_model_dependency", Impact.READ_ONLY, "fixture:model", "data_access"),
        "fingerprint_model_response": Operation("fingerprint_model_response", Impact.READ_ONLY, "fixture:model", "data_access"),
    }

    def _seed_state(self) -> None:
        self.state.update({"identity": "atlas", "version": "2026-09", "adapter": "safety-v3"})

    def _execute(self, op: Operation, params: Mapping[str, Any]) -> dict[str, Any]:
        fixture = str(params.get("fixture", "stable"))
        seed = int(params.get("seed", 0))
        if op.name == "declare_model_dependency":
            declaration = None if fixture == "undeclared" else {
                "model_identity": self.state["identity"],
                "model_version": self.state["version"],
                "fine_tune_or_adapter": self.state["adapter"],
            }
            return {"declaration": declaration, "side_effect": "scenario_local_only"}
        material = "fixed-response" if fixture != "unstable" else f"divergent-response-{seed}"
        return {
            "seed": seed,
            "response_fingerprint": hashlib.sha256(material.encode()).hexdigest(),
            "side_effect": "scenario_local_only",
        }
