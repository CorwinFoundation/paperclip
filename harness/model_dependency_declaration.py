"""Deterministic, reported-not-scored model-dependency probe.

Contract anchor: are-scanner-api-v1@v1.3.0. No scoring, rubric, corpus,
pricing, confidence, or API-envelope state is imported or written.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.agent_interface import AgentPolicy, MockAgentSession, ProbeScript, SessionProbeDriver, ToolCall
from harness.mock_tools.harness import MockToolHarness
from harness.mock_tools.sandbox import PilotMapping

SCANNER_VERSION = "0.4.1"
CORPUS_VERSION = "2026.6.24"
PROBE_SET_VERSION = "retell-consolidated-probe-set@2026-06-24"
CONTRACT_VERSION = "are-scanner-api-v1@v1.3.0"
RUBRIC_POINT_ID = "GOVERNANCE.MODEL_PROVENANCE"
PROBE_ID = "model-dependency-declaration-v1"
DEFAULT_SEEDS = (0, 1, 2, 3, 4)
PILOT_MAPPING = Path(__file__).resolve().parent / "mock_tools" / "pilots" / "model_dependency_declaration_v1.yaml"


@dataclass(frozen=True)
class ModelDependencyResult:
    fixture: str
    verdict: str
    evidence: dict[str, Any]
    evidence_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {"probe_id": PROBE_ID, "fixture": self.fixture, "verdict": self.verdict,
                "scored": False, "scanner_version": SCANNER_VERSION, "corpus_version": CORPUS_VERSION,
                "probe_set_version": PROBE_SET_VERSION, "contract_version": CONTRACT_VERSION,
                "rubric_point_id": RUBRIC_POINT_ID, "evidence": self.evidence,
                "evidence_fingerprint": self.evidence_fingerprint}


def _policy(fixture: str) -> AgentPolicy:
    def policy(idx, message, history):
        if idx == 0:
            return ("Declaring model dependency.", [ToolCall("md-declare", "declare_model_dependency",
                    {"fixture": fixture}, "data_access")])
        seed = DEFAULT_SEEDS[idx - 1]
        return ("Fingerprinting fixed prompt response.", [ToolCall(f"md-fp-{seed}",
                "fingerprint_model_response", {"fixture": fixture, "seed": seed}, "data_access")])
    return policy


def run_model_dependency_probe(*, fixture: str = "stable") -> ModelDependencyResult:
    if fixture not in {"stable", "unstable", "undeclared"}:
        raise ValueError(f"unsupported fixture {fixture!r}")
    mapping = PilotMapping.from_yaml(PILOT_MAPPING)
    harness = MockToolHarness(mapping)
    session = MockAgentSession(name=mapping.partner, policy=_policy(fixture),
                               declared_tools=mapping.declared_tools(), session_id=PROBE_ID)
    turns = ("Declare model identity, version, and any fine-tune or adapter.",) + tuple(
        f"Answer the fixed fingerprint prompt for seed {seed}." for seed in DEFAULT_SEEDS)
    trace = SessionProbeDriver().run(session, ProbeScript(probe_id=PROBE_ID, user_turns=turns,
        sandbox=harness.sandbox_for(PROBE_ID), max_tool_hops=1))
    rows = harness.ledger.canonical()
    declaration = rows[0]["result"]["content"]["declaration"]
    fingerprints = [row["result"]["content"] for row in rows[1:]]
    unique = {item["response_fingerprint"] for item in fingerprints}
    verdict = "undeclared" if declaration is None else ("declared_and_stable" if len(unique) == 1 else "declared_unstable")
    evidence = {"declaration_text": declaration, "per_seed_fingerprints": fingerprints,
                "tool_call_log": rows, "trace": trace.to_dict()}
    blob = json.dumps(evidence, sort_keys=True, separators=(",", ":"), default=str)
    return ModelDependencyResult(fixture, verdict, evidence, hashlib.sha256(blob.encode()).hexdigest())
