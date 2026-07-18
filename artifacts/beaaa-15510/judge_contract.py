"""Strict, fail-closed judge output contract for BEAAA-15510."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable

VERDICTS = ("PASS", "FAIL")
MAX_RATIONALE_CHARS = 1000
SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "rationale": {"type": "string", "minLength": 1, "maxLength": MAX_RATIONALE_CHARS},
    },
    "required": ["verdict", "rationale"],
    "additionalProperties": False,
}


class JudgeOutputError(ValueError):
    """A typed provider/output-contract failure; never a rubric verdict."""

    def __init__(self, error_class: str, message: str):
        super().__init__(message)
        self.error_class = error_class


def _reject_duplicate_keys(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise JudgeOutputError("duplicate_key", f"duplicate key: {key}")
        result[key] = value
    return result


def parse_judge_output(raw: str) -> dict[str, str]:
    """Accept exactly one canonical JSON object; perform no salvage."""
    try:
        parsed = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except JudgeOutputError:
        raise
    except json.JSONDecodeError as exc:
        raise JudgeOutputError("invalid_json", str(exc)) from exc
    if not isinstance(parsed, dict):
        raise JudgeOutputError("wrong_type", "judge output must be an object")
    keys = set(parsed)
    required = {"verdict", "rationale"}
    if missing := required - keys:
        raise JudgeOutputError("missing_field", f"missing fields: {sorted(missing)}")
    if extra := keys - required:
        raise JudgeOutputError("extra_field", f"extra fields: {sorted(extra)}")
    if parsed["verdict"] not in VERDICTS:
        raise JudgeOutputError("invalid_verdict", "verdict is outside the pinned enum")
    rationale = parsed["rationale"]
    if not isinstance(rationale, str) or not 1 <= len(rationale) <= MAX_RATIONALE_CHARS:
        raise JudgeOutputError("invalid_rationale", "rationale must be a bounded non-empty string")
    return {"verdict": parsed["verdict"], "rationale": rationale}


def response_hash(raw: str) -> str:
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class JudgeResult:
    status: str
    verdict: str | None
    rationale: str | None
    attempts: tuple[dict[str, Any], ...]


def run_judge_vote(
    request_once: Callable[[int], str], *, seed: int, max_attempts: int = 2
) -> JudgeResult:
    """Retry the identical semantic request; exhaustion is typed incomplete."""
    attempts: list[dict[str, Any]] = []
    for attempt_number in range(1, max_attempts + 1):
        raw: str | None = None
        try:
            raw = request_once(seed)
            parsed = parse_judge_output(raw)
            attempts.append({
                "attempt": attempt_number, "status": "valid", "error_class": None,
                "response_sha256": response_hash(raw),
            })
            return JudgeResult("complete", parsed["verdict"], parsed["rationale"], tuple(attempts))
        except JudgeOutputError as exc:
            attempts.append({
                "attempt": attempt_number, "status": "invalid", "error_class": exc.error_class,
                "response_sha256": response_hash(raw) if isinstance(raw, str) else None,
            })
        except Exception as exc:
            attempts.append({
                "attempt": attempt_number, "status": "provider_error",
                "error_class": type(exc).__name__, "response_sha256": None,
            })
    return JudgeResult("incomplete", None, None, tuple(attempts))


def anthropic_output_config() -> dict[str, Any]:
    """Pinned strict schema passed to Anthropic's structured-output mode."""
    return {"format": {"type": "json_schema", "schema": SCHEMA}}
