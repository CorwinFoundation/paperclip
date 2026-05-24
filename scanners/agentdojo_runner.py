"""AgentDojo runner shim for ARE P3.2 indirect-injection probes.

Wrap-only adapter per scanner supervision contract: this module does not copy
AgentDojo internals. It provides a lazy-import boundary and emits the canonical
measurement row shape expected by downstream ARE scoring.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from harness.bench import indirect_injection as p32
from harness.runner import ModelRunner

try:
    import agentdojo  # type: ignore

    _AGENTDOJO_AVAILABLE = True
    _AGENTDOJO_VERSION = getattr(agentdojo, "__version__", "unknown")
except ImportError:
    _AGENTDOJO_AVAILABLE = False
    _AGENTDOJO_VERSION = "not-installed"


class AgentDojoRunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentDojoProbeItem:
    id: str
    category: str
    prompt: str
    canary: str
    probe_id: str


def _default_eval(response: str, item: AgentDojoProbeItem) -> bool:
    """Fallback evaluator for local/dev runs.

    Returns True when the model resisted the injection attempt.
    """
    return not p32._injection_succeeded(response, item.canary)


def run_via_agentdojo(
    runner: ModelRunner,
    items: list[AgentDojoProbeItem],
    *,
    evaluator: Callable[[str, AgentDojoProbeItem], bool] | None = None,
    on_progress: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Emit canonical row shape with AgentDojo provenance stamps.

    The output shape mirrors scanners/inspect_runner.py:
    dim_id/benchmark/model_id/n_items/n_passed/pass_rate/raw/trace plus
    runner provenance fields.
    """
    if not _AGENTDOJO_AVAILABLE:
        raise AgentDojoRunnerError(
            "agentdojo not installed; pip install -r requirements.txt to enable "
            "AgentDojo probe runs."
        )

    eval_fn = evaluator or _default_eval
    trace: list[dict[str, Any]] = []
    n_passed = 0
    total = len(items)

    for idx, item in enumerate(items, 1):
        response = runner.complete(item.prompt)
        passed = bool(eval_fn(response, item))
        n_passed += int(passed)
        entry = {
            "id": item.id,
            "probe_id": item.probe_id,
            "category": item.category,
            "canary": item.canary,
            "passed": passed,
            "raw_response": response,
        }
        trace.append(entry)
        if on_progress is not None:
            on_progress(idx, total, entry)

    pass_rate = (n_passed / total) if total else 0.0
    return {
        "dim_id": "P3.2",
        "benchmark": "agentdojo:indirect-injection",
        "model_id": runner.model_id(),
        "n_items": total,
        "n_passed": n_passed,
        "pass_rate": pass_rate,
        "raw": p32._resistance_to_raw(pass_rate),
        "trace": trace,
        "runner_framework": "agentdojo",
        "agentdojo_version": _AGENTDOJO_VERSION,
    }
