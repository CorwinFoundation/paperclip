"""Verify the live edge runtime import closure.

BEAAA-13361 build guard. The live FastAPI edge image must import the API and
live-probe modules it serves, while keeping offline eval frameworks absent from
the image. A failure here fails the Docker build and the PR image-build job.

Keep this module list aligned with the BEAAA-13329 runtime-slice contract in
``requirements-runtime.txt`` and ``tests/test_runtime_import_closure_beaaa13329.py``.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from collections.abc import Sequence
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# api.app constructs the global ASGI app at import time. The build guard is not
# a production boot; a dummy key is enough to prove import closure.
os.environ.setdefault("ARE_BUNDLE_SIGNING_KEY", "beaaa13361-import-closure-dummy")


LIVE_MODULES: tuple[str, ...] = (
    "api.app",
    "api.bundle",
    "api.config",
    "api.edge_gateway",
    "api.runtime_guards",
    "api.scan_handlers",
    "api.scan_log",
    "api.scan_runtime_factory",
    "api.sla",
    "harness.credentials",
    "harness.customer_runner",
    "harness.egress",
    "harness.bench.factuality_golden",
    "harness.bench.p1_3_consistency_golden",
    "harness.bench.p1_4_refusal_golden",
    "harness.bench.p1_5_calibration_golden",
    "harness.runner",
    "pricing.quote",
    "rubric.scoring",
    "scanners.canary_anti_gaming",
    "scanners.customer_agent_walker",
    "scanners.live_probe_orchestrator",
    "scanners.live_probe_workers",
    "scanners.score_runtime",
    "scanners.snapshot_hash",
    "tools.active_test_registry",
    "tools.registered_case_executor",
)

OFFLINE_EVAL_MODULES: tuple[str, ...] = (
    "agentdojo",
    "garak",
    "inspect_ai",
    "pyrit",
)


def _import_live_modules(modules: Sequence[str]) -> list[str]:
    failures: list[str] = []
    for module_name in modules:
        try:
            importlib.import_module(module_name)
        except Exception as exc:  # pragma: no cover - exercised by CI/build
            failures.append(f"{module_name}: {exc.__class__.__name__}: {exc}")
    return failures


def _present_modules(modules: Sequence[str]) -> list[str]:
    return [
        module_name
        for module_name in modules
        if importlib.util.find_spec(module_name) is not None
    ]


def main() -> int:
    failures = _import_live_modules(LIVE_MODULES)
    present_offline = _present_modules(OFFLINE_EVAL_MODULES)

    if failures:
        print("Live runtime import-closure check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)

    if present_offline:
        print(
            "Offline eval frameworks must be absent from the live edge image:",
            file=sys.stderr,
        )
        for module_name in present_offline:
            print(f"  - {module_name}", file=sys.stderr)

    if failures or present_offline:
        return 1

    print(
        "runtime import closure ok: live modules import; offline eval frameworks absent"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
