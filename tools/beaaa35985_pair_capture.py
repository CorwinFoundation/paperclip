"""Importable dispatch + grade step for the BEAAA-33673 capture loop.

This lives outside ``scripts/beaaa33673_capture.py`` because that script
executes its whole run on import: it reads the cohort file, requires
``VAPI_API_KEY`` and constructs a live Vapi runner at module scope. A unit test
therefore cannot reach the loop body in place, and a test that re-implements the
body inline proves nothing about what the script runs. The script imports
``dispatch_and_grade`` from here, so the tested code and the captured code are
the same object.

BEAAA-35985: paired families dispatch both arms (``spec.prompts``) and are
graded by ``judge_pair``, never by the generic single-text judge.
"""
from __future__ import annotations

import dataclasses
import inspect
import json
from typing import Any

from harness.bench.counterfactual_pairs import judge_pair
from tools.content_candidate_inventory import candidate_judge_criteria
from tools.registered_case_executor import _judge_materialized_text_case

# Families whose cases are a demographically-paired A/B comparison. A row in one
# of these families is meaningless unless both arms reach the target.
PAIRED_FAMILIES = frozenset({"counterfactual_stability"})

_PASS_LABELS = frozenset({"stable", "drift_minor"})


def spec_fields(spec: Any) -> dict[str, Any]:
    if dataclasses.is_dataclass(spec):
        return {f.name: getattr(spec, f.name) for f in dataclasses.fields(spec)}
    return {k: v for k, v in vars(spec).items() if not k.startswith("_")}


def ask(subject: Any, prompt: str) -> Any:
    params = list(inspect.signature(subject.complete).parameters)
    try:
        return subject.complete(prompt)
    except TypeError:
        return subject.complete(prompts=[prompt]) if "prompts" in params else subject.complete([prompt])


def text_of(resp: Any) -> str:
    if isinstance(resp, str):
        return resp
    for attr in ("text", "output_text", "content", "response"):
        v = getattr(resp, attr, None) or (resp.get(attr) if isinstance(resp, dict) else None)
        if isinstance(v, str) and v.strip():
            return v
    return json.dumps(resp, default=str)


def dispatch_and_grade(family: str, case_id: str, spec: Any, subject: Any, judge: Any) -> dict[str, Any]:
    """Dispatch every prompt the spec declares, then grade with the right oracle.

    Returns ``{"prompts", "responses", "verdict", "judge"}``. ``spec.prompts`` is
    read off the dataclass directly: the old ``fields.get("prompts")`` fallback
    dropped to the raw catalogue turn, a single dict carrying both arms, so a
    paired case sent one blob and got one response back.
    """
    prompts = list(spec.prompts)
    responses = [text_of(ask(subject, p)) for p in prompts]

    if family in PAIRED_FAMILIES:
        if len(prompts) != 2:
            raise ValueError(f"paired case must materialize two prompts: {family}/{case_id}")
        observable = (spec_fields(spec).get("expected") or {}).get("expected_observable_behavior", {})
        res = judge_pair(
            judge,
            responses[0],
            responses[1],
            underlying_scenario=str(observable.get("scenario_id") or case_id),
            ensemble_n=3,
        )
        res["type"] = "counterfactual_stability_paired_comparator"
        label = res.get("label")
        verdict = "pass" if label in _PASS_LABELS else ("inconclusive" if label == "inconclusive" else "fail")
    else:
        fields = spec_fields(spec)
        res = _judge_materialized_text_case(
            judge, prompts=prompts, responses=responses,
            expected=fields.get("expected") or fields.get("deterministic_oracle") or {},
            judge_criteria=candidate_judge_criteria(spec),
            applicability=fields.get("applicability"),
        )
        verdict = str(res.get("verdict", "")).casefold()

    return {"prompts": prompts, "responses": responses, "verdict": verdict, "judge": res}
