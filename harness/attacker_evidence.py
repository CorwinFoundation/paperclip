"""Evidence persistence and measurement summaries for attacker-v1 runs."""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .attacker_engine import AttackResult


class FrozenCaseError(ValueError):
    """Raised when a success cannot be safely added to a frozen pack."""


class FrozenCaseStore:
    """Append-only JSONL store for successful attacker cases.

    Case ids are the goal ids. A duplicate id is rejected rather than silently
    replacing an earlier transcript, preserving replay provenance.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def _existing_ids(self) -> set[str]:
        if not self.path.exists():
            return set()
        ids: set[str] = set()
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                case_id = row.get("id")
                if not isinstance(case_id, str) or not case_id:
                    raise FrozenCaseError("frozen pack contains a row without an id")
                ids.add(case_id)
        return ids

    def freeze(self, result: AttackResult, *, commit: str) -> Mapping[str, object]:
        if not result.success:
            raise FrozenCaseError("failed attacks are evidence and cannot become tests")
        case = dict(result.frozen_case(commit))
        if case["id"] in self._existing_ids():
            raise FrozenCaseError(f"case already frozen: {case['id']}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(case, sort_keys=True, separators=(",", ":")) + "\n")
        return case


@dataclass(frozen=True)
class ZooMeasurement:
    agent_id: str
    family: str
    attempts: int
    successes: int
    success_rate: float
    median_turns_to_success: float | None
    cost_per_success_usd: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "family": self.family,
            "attempts": self.attempts,
            "successes": self.successes,
            "success_rate": self.success_rate,
            "median_turns_to_success": self.median_turns_to_success,
            "cost_per_success_usd": self.cost_per_success_usd,
        }


def summarize_zoo(agent_id: str, family: str, results: Sequence[AttackResult]) -> ZooMeasurement:
    """Summarize one agent/family slice without inventing a verdict."""
    if not results:
        raise ValueError("cannot summarize an empty zoo slice")
    successes = [result for result in results if result.success]
    turns = [
        sum(turn["role"] == "attacker" for turn in result.turns)
        for result in successes
    ]
    total_cost = sum((result.cost_usd for result in successes), Decimal("0"))
    return ZooMeasurement(
        agent_id=agent_id,
        family=family,
        attempts=len(results),
        successes=len(successes),
        success_rate=len(successes) / len(results),
        median_turns_to_success=statistics.median(turns) if turns else None,
        cost_per_success_usd=(str(total_cost / len(successes)) if successes else None),
    )


def write_zoo_measurements(path: Path, measurements: Iterable[ZooMeasurement]) -> Path:
    """Write the reviewable zoo table as deterministic JSON or JSONL."""
    rows = [measurement.to_dict() for measurement in measurements]
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".jsonl":
        path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )
    elif path.suffix == ".json":
        path.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        raise ValueError("zoo measurements must end in .json or .jsonl")
    return path
