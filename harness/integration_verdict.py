"""Additive per-attempt evidence projection for native integration runners."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import jsonschema

SCHEMA_VERSION = "integration_verdict_v1"
SCHEMA_PATH = Path(__file__).parents[1] / "contracts" / "integration-verdict-v1.schema.json"
OUTCOMES = frozenset({"PASS", "FAIL", "UNSUPPORTED", "ERROR", "INCONCLUSIVE"})


class IntegrationVerdictError(ValueError):
    """Raised when runner output cannot form a trustworthy verdict record."""


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_integration_verdict(record: Mapping[str, Any]) -> None:
    """Validate shape and the semantic prerequisites for a PASS verdict."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        jsonschema.Draft202012Validator(schema).validate(dict(record))
    except jsonschema.ValidationError as exc:
        raise IntegrationVerdictError(exc.message) from exc
    if record["outcome"] == "PASS":
        if record["oracle_evaluated"] is not True:
            raise IntegrationVerdictError("PASS requires an evaluated oracle")
        if not record["evidence_refs"]:
            raise IntegrationVerdictError("PASS requires at least one evidence ref")


def build_integration_verdict(runner_output: Mapping[str, Any]) -> dict[str, Any]:
    """Project one existing runner attempt into ``integration_verdict_v1``.

    The caller supplies its explicit oracle outcome. Transport observations such
    as HTTP status, process exit status, or response text are retained only in
    referenced evidence and are never interpreted as PASS here.
    """
    outcome = runner_output.get("outcome")
    if outcome not in OUTCOMES:
        raise IntegrationVerdictError("runner output requires an explicit supported outcome")
    payload = runner_output.get("payload")
    oracle = runner_output.get("oracle")
    if payload is None or oracle is None:
        raise IntegrationVerdictError("runner output requires payload and oracle definitions")

    record = {
        "schema_version": SCHEMA_VERSION,
        "scenario_id": runner_output.get("scenario_id"),
        "attempt_id": runner_output.get("attempt_id"),
        "engine": runner_output.get("engine"),
        "engine_version": runner_output.get("engine_version"),
        "target_version": runner_output.get("target_version"),
        "identity": deepcopy(runner_output.get("identity")),
        "payload_sha256": canonical_sha256(payload),
        "oracle_sha256": canonical_sha256(oracle),
        "seed": runner_output.get("seed"),
        "actual_turns": runner_output.get("actual_turns", 0),
        "tool_calls": deepcopy(runner_output.get("tool_calls", [])),
        "before_state": deepcopy(runner_output.get("before_state")),
        "after_state": deepcopy(runner_output.get("after_state")),
        "elapsed_ms": runner_output.get("elapsed_ms"),
        "target_calls": runner_output.get("target_calls", 0),
        "judge_calls": runner_output.get("judge_calls", 0),
        "cost_usd": runner_output.get("cost_usd", 0.0),
        "outcome": outcome,
        "reason": runner_output.get("reason"),
        "oracle_evaluated": runner_output.get("oracle_evaluated", False),
        "evidence_refs": list(runner_output.get("evidence_refs", [])),
        "evidence_origin": runner_output.get("evidence_origin"),
        "replay_command": runner_output.get("replay_command"),
    }
    validate_integration_verdict(record)
    return record
