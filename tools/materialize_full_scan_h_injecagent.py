"""Materialize all 1,054 pinned InjecAgent cases for Full Scan H."""
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
from evals.oss_pilots.injecagent import SOURCE_COMMIT
from tools.build_injecagent_records import SOURCES, fetch

DEFAULT_OUTPUT = REPO_ROOT / "evals" / "full_scan_h" / "injecagent.jsonl"
EXPECTED_CASES = 1_054


def reference_cases(sources: dict[str, Iterable[dict[str, Any]]]) -> list[ReferenceCase]:
    cases: list[ReferenceCase] = []
    for kind, rows in sources.items():
        prefix = "dh" if kind == "direct_harm" else "ds"
        for index, row in enumerate(rows):
            cases.append(ReferenceCase(
                source="InjecAgent",
                source_version=SOURCE_COMMIT,
                source_case_id=f"{prefix}.{index:04d}",
                prompt=str(row["User Instruction"]),
                attack_payload=str(row["Tool Response"]),
            ))
    return cases


def materialize(output: Path, backbond_prompts: Iterable[tuple[str, str]] = ()) -> dict[str, Any]:
    sources = {kind: fetch(path, digest) for kind, (path, digest) in SOURCES.items()}
    imported = reference_cases(sources)
    if len(imported) != EXPECTED_CASES:
        raise ValueError(f"expected {EXPECTED_CASES} pinned cases, got {len(imported)}")
    kept, dropped = deduplicate(imported, backbond_prompts)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for case in kept:
            handle.write(json.dumps(case.registry_row(), sort_keys=True, separators=(",", ":")) + "\n")
    return {
        "source": "InjecAgent",
        "source_version": SOURCE_COMMIT,
        "source_license": "MIT",
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
