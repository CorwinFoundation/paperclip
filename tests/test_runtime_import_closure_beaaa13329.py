"""BEAAA-13329 — production runtime import-closure guard.

Mechanical guarantee that ``requirements-runtime.txt`` stays a *true superset*
of the live production import closure while EXCLUDING the four offline-only
eval frameworks (garak / pyrit / agentdojo / inspect-ai).

Two invariants:

1. Every module the live service imports at startup — the FastAPI app plus the
   live customer-scan chain — must import successfully under the runtime slice.
   If a live dependency is dropped from ``requirements-runtime.txt`` this fails.

2. The four offline eval frameworks must NOT be importable from the runtime
   slice. They are imported only by ``scanners/{pyrit,agentdojo,inspect}_runner``
   (each a soft ``try/except ImportError``) and the promptfoo Node path (garak);
   none are in the live runtime closure. If one leaks back into the production
   venv this fails.

Invariant (2) is only meaningful inside a venv built from
``requirements-runtime.txt`` alone, so it is gated behind the
``ARE_RUNTIME_SLICE_VENV=1`` marker that the dedicated CI job sets. In a normal
dev/test environment (where the eval frameworks may legitimately be installed)
it skips, while invariant (1) still runs and carries value everywhere.

See ADR-0038 (decisions/ADR-0038-runtime-requirements-slice.md).
"""

from __future__ import annotations

import importlib
import os

import pytest

# The live production import closure (api/* + the live customer-scan chain).
# Keep in lockstep with ADR-0038 and requirements-runtime.txt's header.
LIVE_MODULES = (
    "api.app",
    "scanners.live_probe_orchestrator",
    "scanners.live_probe_workers",
    "scanners.score_runtime",
    "scanners.customer_agent_walker",
    "harness.runner",
    "harness.customer_runner",
    "harness.credentials",
    "harness.egress",
)

# The four offline-only eval frameworks that MUST be absent from the prod venv.
# (Import module names — note inspect-ai's import name is ``inspect_ai``.)
EXCLUDED_FRAMEWORKS = ("garak", "pyrit", "agentdojo", "inspect_ai")

_SLICE_VENV = os.environ.get("ARE_RUNTIME_SLICE_VENV") == "1"


def _ensure_prod_env() -> None:
    """Set the minimal env that ``api.app`` requires to import in prod posture."""
    # api/config.py raises unless a bundle signing key is present; a dummy value
    # is sufficient to exercise module import (no signing happens at import).
    os.environ.setdefault("ARE_BUNDLE_SIGNING_KEY", "beaaa13329-import-closure-dummy")


@pytest.mark.parametrize("module_name", LIVE_MODULES)
def test_live_module_imports_under_runtime_slice(module_name: str) -> None:
    """Every live-service module imports successfully (superset invariant)."""
    _ensure_prod_env()
    importlib.import_module(module_name)


@pytest.mark.skipif(
    not _SLICE_VENV,
    reason="eval-framework absence is only asserted in the runtime-slice venv "
    "(set ARE_RUNTIME_SLICE_VENV=1; the dedicated CI job does this)",
)
@pytest.mark.parametrize("framework", EXCLUDED_FRAMEWORKS)
def test_eval_framework_not_importable_in_runtime_slice(framework: str) -> None:
    """The four offline eval frameworks must NOT be present in the prod venv."""
    with pytest.raises(ImportError):
        importlib.import_module(framework)
