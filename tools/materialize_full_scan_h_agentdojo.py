"""Materialize the 629 AgentDojo 0.1.35 security cases for Full Scan H."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evals.full_scan_h_reference_imports import ReferenceCase, deduplicate

SOURCE_VERSION = "0.1.35"
BENCHMARK_VERSION = "v1.2.2"
SOURCE_LICENSE = "MIT"
EXPECTED_CASES = 629
SUITES = ("workspace", "slack", "travel", "banking")
# AgentDojo's published 629-case inventory uses the original six workspace
# injection targets. Package 0.1.35 retains eight later workspace additions;
# those are outside the pinned Full Scan H reference inventory.
PUBLISHED_INJECTION_COUNTS = {"workspace": 6, "slack": 5, "travel": 7, "banking": 9}
DEFAULT_OUTPUT = REPO_ROOT / "evals" / "full_scan_h" / "agentdojo.jsonl"


def load_suites() -> dict[str, Any]:
    try:
        from agentdojo.task_suite.load_suites import get_suite
    except ImportError as exc:
        raise RuntimeError(
            "agentdojo==0.1.35 is required; run this tool with the pinned framework venv"
        ) from exc
    return {name: get_suite(BENCHMARK_VERSION, name) for name in SUITES}


def reference_cases(suites: dict[str, Any]) -> list[ReferenceCase]:
    cases: list[ReferenceCase] = []
    for suite_name in SUITES:
        suite = suites[suite_name]
        injection_ids = list(suite.injection_tasks)[: PUBLISHED_INJECTION_COUNTS[suite_name]]
        for user_id, user_task in suite.user_tasks.items():
            for injection_id in injection_ids:
                injection_task = suite.injection_tasks[injection_id]
                cases.append(
                    ReferenceCase(
                        source="AgentDojo",
                        source_version=SOURCE_VERSION,
                        source_case_id=f"{suite_name}.{user_id}.{injection_id}",
                        prompt=str(user_task.PROMPT),
                        attack_payload=str(injection_task.GOAL),
                    )
                )
    return cases


def materialize(
    output: Path, backbond_prompts: Iterable[tuple[str, str]] = ()
) -> dict[str, Any]:
    imported = reference_cases(load_suites())
    if len(imported) != EXPECTED_CASES:
        raise ValueError(f"expected {EXPECTED_CASES} pinned cases, got {len(imported)}")
    kept, dropped = deduplicate(imported, backbond_prompts)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for case in kept:
            handle.write(json.dumps(case.registry_row(), sort_keys=True, separators=(",", ":")) + "\n")
    return {
        "source": "AgentDojo",
        "source_version": SOURCE_VERSION,
        "benchmark_version": BENCHMARK_VERSION,
        "source_license": SOURCE_LICENSE,
        "source_cases": len(imported),
        "materialized_cases": len(kept),
        "deduplicated_cases": len(dropped),
        "deduplicated": dropped,
        "output": str(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(materialize(args.output), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
