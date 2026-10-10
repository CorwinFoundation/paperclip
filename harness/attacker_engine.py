"""Budgeted, goal-directed multi-turn attacker for Full Scan.

The engine owns orchestration and evidence only. Model adapters supply attack
turns and judge decisions, which keeps provider credentials and model-specific
SDKs outside the core loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Mapping, Protocol, Sequence


STRATEGIES = ("crescendo", "pair", "tap", "goat")
ADAPTIVE_STRATEGY = "adaptive"


class AttackerBudgetError(ValueError):
    """Raised when an attacker budget or generated turn is invalid."""


@dataclass(frozen=True)
class AttackGoal:
    goal_id: str
    family: str
    objective: str
    severity: str


@dataclass(frozen=True)
class AttackBudget:
    max_turns: int
    max_usd: Decimal

    def __post_init__(self) -> None:
        if self.max_turns < 1 or self.max_usd <= 0:
            raise AttackerBudgetError("max_turns and max_usd must be positive")


@dataclass(frozen=True)
class JudgeDecision:
    reached: bool
    reason: str
    cost_usd: Decimal = Decimal("0")


@dataclass(frozen=True)
class GeneratedTurn:
    content: str
    cost_usd: Decimal


@dataclass(frozen=True)
class TargetReply:
    """Target output plus the provider charge for that target call.

    The engine cannot enforce a hard spend cap when a target adapter hides its
    per-call cost. Adapters therefore return this envelope; plain strings are
    still accepted for deterministic unit tests and are treated as zero-cost.
    """

    content: str
    cost_usd: Decimal = Decimal("0")


class TargetAdapter(Protocol):
    """Minimal target-deployment boundary used by the adaptive loop."""

    model_id: str

    def estimate_cost_usd(self, prompt: str) -> Decimal: ...

    def send(self, prompt: str) -> TargetReply: ...


class JudgeAdapter(Protocol):
    """Pinned judge boundary with a preflight cost estimate."""

    model_id: str

    def estimate_cost_usd(
        self, goal: AttackGoal, transcript: Sequence[Mapping[str, str]]
    ) -> Decimal: ...

    def decide(
        self, goal: AttackGoal, transcript: Sequence[Mapping[str, str]]
    ) -> JudgeDecision: ...


@dataclass
class AttackResult:
    goal: AttackGoal
    strategy: str
    attacker_model: str
    judge_model: str
    target_model: str | None = None
    success: bool = False
    stop_reason: str = "turn_budget_exhausted"
    cost_usd: Decimal = Decimal("0")
    turns: list[dict[str, str]] = field(default_factory=list)
    judge_reasons: list[str] = field(default_factory=list)
    strategy_trace: list[str] = field(default_factory=list)
    target_cost_usd: Decimal = Decimal("0")

    def frozen_case(self, commit: str) -> Mapping[str, object]:
        if not self.success:
            raise ValueError("failed attacks are evidence and cannot become tests")
        return {
            "id": self.goal.goal_id,
            "family": self.goal.family,
            "severity": self.goal.severity,
            "provenance": f"generated:attacker-v1@{commit}",
            "strategy": self.strategy,
            "attacker_model": self.attacker_model,
            "judge_model": self.judge_model,
            "target_model": self.target_model,
            "strategy_trace": list(self.strategy_trace),
            "cost_usd": str(self.cost_usd),
            "judge_reasons": list(self.judge_reasons),
            "turns": list(self.turns),
        }

    def evidence_record(self) -> Mapping[str, object]:
        """Return a complete, non-reporting evidence row for this attempt."""
        return {
            "goal_id": self.goal.goal_id,
            "family": self.goal.family,
            "success": self.success,
            "stop_reason": self.stop_reason,
            "strategy": self.strategy,
            "strategy_trace": list(self.strategy_trace),
            "attacker_model": self.attacker_model,
            "judge_model": self.judge_model,
            "turn_count": sum(turn["role"] == "attacker" for turn in self.turns),
            "cost_usd": str(self.cost_usd),
            "target_cost_usd": str(self.target_cost_usd),
            "judge_reasons": list(self.judge_reasons),
            "turns": list(self.turns),
        }


Attacker = Callable[[AttackGoal, str, Sequence[Mapping[str, str]]], GeneratedTurn]
Target = Callable[[str], str]
Judge = Callable[[AttackGoal, Sequence[Mapping[str, str]]], JudgeDecision]
StrategySelector = Callable[[AttackGoal, Sequence[Mapping[str, str]], Sequence[str]], str]


def adaptive_strategy(
    _goal: AttackGoal,
    transcript: Sequence[Mapping[str, str]],
    judge_reasons: Sequence[str],
) -> str:
    """Choose a tactic from feedback, progressing from rapport to branching.

    This is deliberately deterministic. A model-backed attacker still chooses
    the actual wording, while the harness records the tactic selected for each
    turn and makes strategy switching reproducible in replay evidence.
    """
    if not transcript:
        return "crescendo"
    latest = (judge_reasons[-1] if judge_reasons else "").lower()
    if any(token in latest for token in ("refus", "deny", "blocked")):
        return "pair"
    if len(transcript) >= 4:
        return "goat"
    return "tap"


def run_attack(
    goal: AttackGoal,
    *,
    strategy: str = ADAPTIVE_STRATEGY,
    budget: AttackBudget,
    attacker_model: str,
    judge_model: str,
    attacker: Attacker,
    target: Target | TargetAdapter,
    judge: Judge,
    target_model: str | None = None,
    strategy_selector: StrategySelector = adaptive_strategy,
) -> AttackResult:
    """Run one adaptive session, stopping before either hard budget is crossed."""
    if strategy not in (*STRATEGIES, ADAPTIVE_STRATEGY):
        raise ValueError(f"unsupported strategy: {strategy}")
    if not attacker_model or not judge_model:
        raise ValueError("pinned attacker_model and judge_model are required")
    resolved_target_model = target_model or getattr(target, "model_id", None)
    if resolved_target_model and resolved_target_model in {attacker_model, judge_model}:
        raise ValueError("attacker and judge must be distinct from the target model")
    result = AttackResult(
        goal, strategy, attacker_model, judge_model, target_model=resolved_target_model
    )
    for _ in range(budget.max_turns):
        selected_strategy = (
            strategy
            if strategy != ADAPTIVE_STRATEGY
            else strategy_selector(goal, result.turns, result.judge_reasons)
        )
        if selected_strategy not in STRATEGIES:
            raise ValueError(f"strategy selector returned unsupported strategy: {selected_strategy}")
        generated = attacker(goal, selected_strategy, result.turns)
        if not generated.content.strip() or generated.cost_usd < 0:
            raise AttackerBudgetError("attacker returned an invalid turn")
        if result.cost_usd + generated.cost_usd > budget.max_usd:
            result.stop_reason = "usd_budget_exhausted"
            return result
        target_estimate = Decimal("0")
        if hasattr(target, "estimate_cost_usd"):
            target_estimate = target.estimate_cost_usd(generated.content)  # type: ignore[union-attr]
            if target_estimate < 0:
                raise AttackerBudgetError("target returned a negative cost estimate")
            if result.cost_usd + generated.cost_usd + target_estimate > budget.max_usd:
                result.stop_reason = "usd_budget_exhausted"
                return result
        response = target.send(generated.content) if hasattr(target, "send") else target(generated.content)  # type: ignore[operator]
        if isinstance(response, str):
            response = TargetReply(response)
        if not response.content.strip() or response.cost_usd < 0:
            raise AttackerBudgetError("target returned an invalid response")
        if hasattr(target, "estimate_cost_usd") and response.cost_usd > target_estimate:
            raise AttackerBudgetError("target cost exceeded its preflight estimate")
        result.cost_usd += generated.cost_usd
        result.target_cost_usd += response.cost_usd
        if result.cost_usd + response.cost_usd > budget.max_usd:
            result.stop_reason = "usd_budget_exhausted"
            return result
        result.cost_usd += response.cost_usd
        result.turns.append({"role": "attacker", "content": generated.content})
        result.turns.append({"role": "target", "content": response.content})
        result.strategy_trace.append(selected_strategy)
        judge_estimate = Decimal("0")
        if hasattr(judge, "estimate_cost_usd"):
            judge_estimate = judge.estimate_cost_usd(goal, result.turns)  # type: ignore[union-attr]
            if judge_estimate < 0:
                raise AttackerBudgetError("judge returned a negative cost estimate")
            if result.cost_usd + judge_estimate > budget.max_usd:
                result.stop_reason = "usd_budget_exhausted"
                return result
        decision = judge.decide(goal, result.turns) if hasattr(judge, "decide") else judge(goal, result.turns)  # type: ignore[operator]
        if decision.cost_usd < 0:
            raise AttackerBudgetError("judge returned a negative cost")
        if hasattr(judge, "estimate_cost_usd") and decision.cost_usd > judge_estimate:
            raise AttackerBudgetError("judge cost exceeded its preflight estimate")
        if result.cost_usd + decision.cost_usd > budget.max_usd:
            result.stop_reason = "usd_budget_exhausted"
            return result
        result.cost_usd += decision.cost_usd
        result.judge_reasons.append(decision.reason)
        if decision.reached:
            result.success = True
            result.stop_reason = "goal_reached"
            return result
    return result
