#!/usr/bin/env python3
"""Evidence-capture harness for external eval-framework probes (BEAAA-WAVE1B-DRAFT).

A probe only counts as running (production_runner_executable) once it carries a
full `execution_evidence` block — concrete_input_or_action, expected_oracle,
recorded_target_response, verdict, github_provenance — and by contract that block
is the record of a REAL run against a target. This harness produces those blocks
by driving the existing wrap-only runner shims against a configured target; it
never fabricates evidence and never touches the scoring path (reported-not-scored).

Usage:
    # list every framework probe that is target-bound but evidence-pending
    python -m harness.eval_framework_capture --plan

    # capture evidence for one framework against the configured target
    #   requires SCANNER_TARGET_URL / SCANNER_TARGET_MODEL / SCANNER_TARGET_KEY
    python -m harness.eval_framework_capture --capture agentdojo --provenance BEAAA-XXXXX

Fail-closed: if no target is configured, --capture refuses rather than writing a
placeholder. The gate flips on its own once real evidence is committed.
"""
from __future__ import annotations

import argparse
import os
from typing import Any, Callable

from tools.active_test_registry import build_registry

# Framework families this harness knows how to capture, mapped to the runner
# seam that executes one probe against the target and returns (response, verdict).
# The callables are wired to the existing wrap-only shims in scanners/ at capture
# time; kept as names here so --plan works without importing framework deps.
CAPTURE_BACKENDS: dict[str, str] = {
    "agentdojo": "scanners.agentdojo_runner",          # run_via_agentdojo / tool_misuse
    "garak": "scanners.promptfoo_runner",              # corpus prompt -> target -> oracle
    "garak_beaaa152": "scanners.promptfoo_runner",
    "jailbreakbench": "scanners.promptfoo_runner",
    "privacybench": "scanners.promptfoo_runner",
    "pyrit": "scanners.pyrit_runner",
    # bias metrics need the dedicated bias runner (separate module)
    "sentiment_skew": "scanners.bias_metric_runner",
    "demographic_parity": "scanners.bias_metric_runner",
    "equal_opportunity": "scanners.bias_metric_runner",
    "counterfactual_stability": "scanners.bias_metric_runner",
    "eval_representation": "scanners.bias_metric_runner",
}

EVIDENCE_FIELDS = (
    "concrete_input_or_action",
    "expected_oracle",
    "recorded_target_response",
    "verdict",
    "github_provenance",
)


class TargetNotConfigured(RuntimeError):
    """Raised when a capture is attempted without a real target — fail closed."""


def target_config() -> dict[str, str]:
    cfg = {
        "url": os.environ.get("SCANNER_TARGET_URL", ""),
        "model": os.environ.get("SCANNER_TARGET_MODEL", ""),
        "key": os.environ.get("SCANNER_TARGET_KEY", ""),
    }
    if not (cfg["url"] and cfg["model"] and cfg["key"]):
        raise TargetNotConfigured(
            "capture requires SCANNER_TARGET_URL, SCANNER_TARGET_MODEL and "
            "SCANNER_TARGET_KEY — refusing to write evidence without a real run"
        )
    return cfg


def pending_probes(framework: str | None = None) -> list[dict[str, Any]]:
    """Framework probes that are target-bound but not yet execution-evidenced."""
    rows = build_registry(include_content_candidates=False)
    out = []
    for r in rows:
        fam = r.get("test_id")
        if fam not in CAPTURE_BACKENDS:
            continue
        if framework and fam != framework:
            continue
        if r.get("execution_evidenced") is True:
            continue
        out.append(r)
    return out


def plan() -> dict[str, list[str]]:
    """Group the evidence-pending probes by framework (for --plan)."""
    grouped: dict[str, list[str]] = {}
    for r in pending_probes():
        grouped.setdefault(str(r.get("test_id")), []).append(str(r.get("case_id")))
    return {k: sorted(v) for k, v in sorted(grouped.items())}


def build_evidence_block(
    *,
    concrete_input: str,
    expected_oracle: str,
    recorded_response: str,
    verdict: str,
    provenance: str,
) -> dict[str, str]:
    """Assemble a complete execution_evidence block. All fields are required;
    recorded_response/verdict must come from a real run (never synthesized here)."""
    block = {
        "concrete_input_or_action": concrete_input,
        "expected_oracle": expected_oracle,
        "recorded_target_response": recorded_response,
        "verdict": verdict,
        "github_provenance": provenance,
    }
    missing = [f for f in EVIDENCE_FIELDS if not str(block.get(f) or "").strip()]
    if missing:
        raise ValueError(f"incomplete execution_evidence: missing {missing}")
    return block


def capture(framework: str, provenance: str, runner: Callable | None = None) -> int:
    """Capture evidence for one framework against the configured target.

    `runner` is the per-probe execution seam (probe -> {response, verdict});
    when omitted it is imported from CAPTURE_BACKENDS[framework] at call time so
    --plan never needs the framework dependency installed. Fail-closed on no target.
    """
    if framework not in CAPTURE_BACKENDS:
        raise ValueError(f"unknown framework {framework!r}; known: {sorted(CAPTURE_BACKENDS)}")
    target_config()  # raises TargetNotConfigured if absent
    todo = pending_probes(framework)
    if not todo:
        print(f"{framework}: nothing pending")
        return 0
    if runner is None:  # real capture path — wired in the company eval CI
        raise NotImplementedError(
            f"wire CAPTURE_BACKENDS[{framework!r}] = {CAPTURE_BACKENDS[framework]} to a "
            "target-bound runner in your eval CI; this harness will not invent responses"
        )
    captured = 0
    for probe in todo:
        result = runner(probe, target_config())  # {response, verdict, input, oracle}
        block = build_evidence_block(
            concrete_input=result["input"],
            expected_oracle=result["oracle"],
            recorded_response=result["response"],
            verdict=result["verdict"],
            provenance=provenance,
        )
        # writer that merges `block` into rubric/probe_mappings/<framework>.yaml
        # for probe['case_id'] is provided by the mapping-writer (kept out of this
        # module so evidence assembly and file mutation stay separately testable).
        _ = block
        captured += 1
    print(f"{framework}: captured evidence for {captured} probes")
    return captured


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", action="store_true", help="list evidence-pending framework probes")
    ap.add_argument("--capture", metavar="FRAMEWORK", help="capture evidence for one framework")
    ap.add_argument("--provenance", default="", help="BEAAA ticket ref recorded in evidence")
    args = ap.parse_args()
    if args.plan or not args.capture:
        grouped = plan()
        total = sum(len(v) for v in grouped.values())
        print(f"evidence-pending framework probes: {total}")
        for fam, cases in grouped.items():
            print(f"  {fam:24} {len(cases):>3}  {', '.join(cases[:6])}{' …' if len(cases) > 6 else ''}")
        return 0
    if not args.provenance:
        raise SystemExit("--capture requires --provenance BEAAA-XXXXX")
    return 0 if capture(args.capture, args.provenance) >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
