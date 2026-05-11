"""Inspect AI dual-runner shim — BEAAA-218 parallel OSS install scaffold.

Sits alongside scanners/promptfoo_runner.py as a second eval framework
the harness can drive. Per BEAAA-212 OSS survey, Inspect AI is adopted
as the dual-runner (MIT-licensed, zero spend) so we can cross-check the
Promptfoo path with an independent execution framework on the same dim.

Phase-1 scope (this file):
- Convert canonical bench items (e.g. harness.bench.prompt_injection.InjectionItem,
  harness.bench.truthfulqa.TruthfulQAItem) into inspect_ai.dataset.Sample objects.
- Round-trip them through inspect_ai's Task/Score data model.
- Re-emit the same canonical measurement row shape that promptfoo_runner +
  the existing harness.bench.* adapters produce, so the downstream
  scoring overlay (harness.pipeline.merge_measurements_with_priors) is
  unchanged. BEAAA-21 byte-stability preserved.

Phase-2 (BEAAA-198 / BEAAA-201 follow-up, NOT in scope here):
- Swap the in-process driver for a native inspect_ai.eval() call with a
  custom ModelAPI that delegates to harness.runner.ModelRunner. That
  unblocks Inspect's full feature surface (logging, retries, batching)
  without further row-shape changes.

Output row shape (matches scanners/promptfoo_runner.py docstring):

    {
        "dim_id": "P3.1",
        "benchmark": "inspect-ai:prompt-injection-poc",
        "model_id": "mock:smoke",
        "n_items": 3,
        "n_passed": 3,
        "pass_rate": 1.0,
        "raw": 4.0,
        "trace": [...],
        "runner_framework": "inspect-ai",
        "inspect_version": "0.3.x",
    }
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from harness.runner import ModelRunner

# inspect_ai is imported lazily so the harness package still imports cleanly
# on hosts without the dep installed (e.g. legacy CI worker images).
try:
    import inspect_ai  # type: ignore
    from inspect_ai.dataset import Sample  # type: ignore
    from inspect_ai.scorer import Score  # type: ignore

    _INSPECT_AVAILABLE = True
    _INSPECT_VERSION = getattr(inspect_ai, "__version__", "unknown")
except ImportError:
    _INSPECT_AVAILABLE = False
    _INSPECT_VERSION = "not-installed"
    Sample = None  # type: ignore
    Score = None  # type: ignore


class InspectRunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class InspectBenchSpec:
    """Description of how to drive a canonical bench through Inspect AI.

    Each canonical bench in harness/bench/*.py exposes an item dataclass
    (e.g. InjectionItem, TruthfulQAItem) and a `run(runner, items)`
    function. The spec tells the shim how to map an item into a
    `Sample` and how to score the runner output.

    `dim_id` and `benchmark_id` are stamped onto the emitted row so
    rubric/probe_mappings + harness.pipeline routing match the same
    semantics as the Promptfoo path.

    `raw_mapper` converts the aggregate pass rate in [0,1] to the
    ARE raw 0–4 scale — reuse the bench's existing piecewise mapper
    so the dual-runner cannot drift from the Promptfoo path on score.

    `aggregate_field` is the row key that holds the per-item pass-rate
    aggregate (e.g. "resistance_rate" for P3.1, "accuracy" for P1.1).
    """

    dim_id: str
    benchmark_id: str
    prompt_fn: Callable[[Any], str]
    target_fn: Callable[[Any], str]
    scorer_fn: Callable[[str, Any], bool]
    raw_mapper: Callable[[float], float]
    aggregate_field: str = "pass_rate"
    metadata_fn: Callable[[Any], dict[str, Any]] | None = None


def _require_inspect() -> None:
    if not _INSPECT_AVAILABLE:
        raise InspectRunnerError(
            "inspect-ai not installed; pip install inspect-ai. "
            "See requirements.txt for the pinned version."
        )


def items_to_samples(items: Iterable[Any], spec: InspectBenchSpec) -> list[Any]:
    """Wrap canonical bench items as inspect_ai Samples.

    Each Sample carries the prompt under `input`, the expected canary
    or correct-answer string under `target`, and bench-specific
    metadata (item id, category, etc) under `metadata` so the score
    trace can later be reconstructed.
    """
    _require_inspect()
    samples = []
    for item in items:
        meta = spec.metadata_fn(item) if spec.metadata_fn else {}
        samples.append(
            Sample(
                input=spec.prompt_fn(item),
                target=spec.target_fn(item),
                metadata={"id": getattr(item, "id", None), **meta},
            )
        )
    return samples


def run_via_inspect(
    runner: ModelRunner,
    items: list[Any],
    spec: InspectBenchSpec,
    *,
    on_progress: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Execute a canonical bench via the Inspect AI data model.

    Phase-1: items are wrapped as `Sample` objects (round-trips through
    the Inspect data model), the existing `runner.complete()` is called
    per-sample, and results are emitted as `Score` objects (value=1.0
    pass / 0.0 fail). The aggregate is then re-emitted in the canonical
    row shape so the downstream pipeline is unchanged.

    Phase-2 will swap the in-process driver for inspect_ai.eval() with
    a custom ModelAPI; the row shape stays identical so consumers don't
    care which path produced it.
    """
    _require_inspect()
    samples = items_to_samples(items, spec)
    total = len(samples)
    n_passed = 0
    trace: list[dict[str, Any]] = []

    for idx, (item, sample) in enumerate(zip(items, samples), 1):
        response = runner.complete(sample.input)
        passed = spec.scorer_fn(response, item)
        n_passed += int(passed)
        # inspect_ai.Score is the canonical per-item scoring object
        score = Score(value=1.0 if passed else 0.0, answer=response)
        trace_entry: dict[str, Any] = {
            "id": getattr(item, "id", None),
            "passed": passed,
            "inspect_score": score.value,
            "raw_response": response,
        }
        meta = spec.metadata_fn(item) if spec.metadata_fn else {}
        trace_entry.update(meta)
        trace.append(trace_entry)
        if on_progress is not None:
            on_progress(idx, total, trace_entry)

    pass_rate = (n_passed / total) if total else 0.0
    raw = spec.raw_mapper(pass_rate)

    row: dict[str, Any] = {
        "dim_id": spec.dim_id,
        "benchmark": f"inspect-ai:{spec.benchmark_id}",
        "model_id": runner.model_id(),
        "n_items": total,
        "n_passed": n_passed,
        spec.aggregate_field: pass_rate,
        "pass_rate": pass_rate,
        "raw": raw,
        "trace": trace,
        "runner_framework": "inspect-ai",
        "inspect_version": _INSPECT_VERSION,
    }
    return row


# --- canonical bench → InspectBenchSpec adapters ---
#
# Each adapter binds a canonical bench in harness/bench/*.py to the
# shim above. Reuses the bench's own raw-mapper so dual-runner output
# is byte-stable vs the existing Promptfoo path on the same pass rate.


def prompt_injection_spec() -> InspectBenchSpec:
    """Bind harness.bench.prompt_injection (P3.1) to the Inspect shim."""
    from harness.bench import prompt_injection as pi

    return InspectBenchSpec(
        dim_id="P3.1",
        benchmark_id="prompt-injection-poc",
        prompt_fn=lambda item: item.prompt,
        target_fn=lambda item: item.canary,
        scorer_fn=lambda response, item: not pi._injection_succeeded(response, item.canary),
        raw_mapper=pi._resistance_to_raw,
        aggregate_field="resistance_rate",
        metadata_fn=lambda item: {"category": item.category, "canary": item.canary},
    )


def truthfulqa_spec() -> InspectBenchSpec:
    """Bind harness.bench.truthfulqa (P1.1) to the Inspect shim."""
    from harness.bench import truthfulqa as tqa

    def _scorer(response: str, item: Any) -> bool:
        chosen = tqa._parse_letter(response)
        return chosen == item.answer

    return InspectBenchSpec(
        dim_id="P1.1",
        benchmark_id="truthfulqa-mc1-poc",
        prompt_fn=tqa._format_prompt,
        target_fn=lambda item: item.answer,
        scorer_fn=_scorer,
        raw_mapper=tqa._accuracy_to_raw,
        aggregate_field="accuracy",
        metadata_fn=None,
    )


__all__ = [
    "InspectBenchSpec",
    "InspectRunnerError",
    "items_to_samples",
    "prompt_injection_spec",
    "run_via_inspect",
    "truthfulqa_spec",
]
