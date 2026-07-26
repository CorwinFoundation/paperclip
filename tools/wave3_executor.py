"""Non-mock, budgeted Wave 3 execution path for BEAAA-18004.

Importing and constructing configuration makes no network calls. Execution
requires an explicit caller-provided Syllable runner.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from tools.registered_case_executor import BudgetExceeded, ModelRunner, ScanBudget, ScanBudgetLedger
from tools.wave3_first100 import MANIFEST, SELECTION, load_jsonl


def wave3_budget() -> ScanBudget:
    return ScanBudget(
        max_target_calls=136,
        max_judge_calls=100,
        max_estimated_judge_cost_usd=100.0,
        max_wall_clock_seconds=8 * 60 * 60,
    )


class AppendOnlyScrubbedEvidence:
    """Seven-year append-only evidence sink with fail-closed field scrubbing."""

    PII_KEYS = {"email", "phone", "name", "address", "ssn", "user_id"}
    EXCLUDED_KEYS = {"proprietary_prompt", "customer_weights"}

    def __init__(self, root: Path) -> None:
        self.root = root

    def _scrub(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            clean: dict[str, Any] = {}
            for key, child in value.items():
                lowered = str(key).lower()
                if lowered in self.EXCLUDED_KEYS:
                    continue
                clean[str(key)] = "[REDACTED]" if lowered in self.PII_KEYS else self._scrub(child)
            return clean
        if isinstance(value, list):
            return [self._scrub(item) for item in value]
        return value

    def write(self, stable_case_id: str, evidence: Mapping[str, Any]) -> Path:
        target = self.root / f"{stable_case_id}.json"
        if target.exists():
            raise FileExistsError(f"append-only evidence exists: {stable_case_id}")
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = self._scrub(dict(evidence))
        payload["retention"] = {"mode": "append_only", "years": 7}
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        payload["redacted_evidence_sha256"] = hashlib.sha256(encoded).hexdigest()
        target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return target


class Wave3Executor:
    def __init__(
        self,
        *,
        subject: ModelRunner,
        judge: ModelRunner,
        evidence: AppendOnlyScrubbedEvidence,
        deterministic_oracle: Callable[[Mapping[str, Any], str], Mapping[str, Any] | None],
    ) -> None:
        self.subject = subject
        self.judge = judge
        self.evidence = evidence
        self.deterministic_oracle = deterministic_oracle
        self.ledger = ScanBudgetLedger(wave3_budget())

    def run_case(self, row: Mapping[str, Any]) -> Path:
        prompts = row["normalized_prompt_or_action"]
        prompt = str(prompts.get("prompt") or prompts.get("prompt_text") or json.dumps(prompts, sort_keys=True))
        self.ledger.reserve_target_call()
        response = self.subject.complete(prompt)
        verdict = self.deterministic_oracle(row, response)
        if verdict is None:
            judge_prompt = json.dumps(
                {"rubric_version": "v1.3.0", "expected": row["expected_behavior"], "response": response},
                sort_keys=True,
            )
            self.ledger.reserve_judge_call(judge_prompt)
            verdict = {"judge_response": self.judge.complete(judge_prompt)}
        return self.evidence.write(
            str(row["stable_case_id"]),
            {
                "stable_case_id": row["stable_case_id"],
                "dedupe_sha256": row["dedupe_sha256"],
                "target_adapter": "syllable:apichat@authenticated-v1",
                "judge_pin": "anthropic:claude-haiku-4-5-20251001",
                "seed": 42,
                "rubric_version": "v1.3.0",
                "response": response,
                "verdict": verdict,
                "budget": self.ledger.snapshot(),
            },
        )


def selected_rows() -> list[dict[str, Any]]:
    config = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = load_jsonl(SELECTION)
    if len(rows) != config["selection"]["count"]:
        raise ValueError("Wave 3 selection/config mismatch")
    return rows
