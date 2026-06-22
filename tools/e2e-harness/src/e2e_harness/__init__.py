"""BackBond E2E production-like test harness (BEAAA-7427).

Vendor-agnostic harness that drives the full chain a real customer scan
would exercise — install -> load context -> run adversarial traffic ->
capture -> ARE scan + score -> Monitor post-bind log->claim — against a
self-serve CX AI vendor we sign up for, in SHADOW / no-pay mode.

Firewall discipline (mirrors the internal dogfood, BEAAA-7320 / 7346):
this package is a *test driver only*. It imports the real scanner probe
families, the real scoring entrypoint, and the real post-bind Monitor
pipeline, but it MUST NOT import or mutate any pricing / portal / band /
rubric-weight code. It moves no golden score (Delta=0); it only *exercises*
the shipped scanner against a test target that is explicitly not customer
traffic.

The live run is push-button: pick a vendor adapter, supply scoped
credentials, and ``e2e_harness.runbook`` executes the whole loop and emits
a measured report. Until a vendor is picked, the kit + the top-3 capture
adapters (Retell AI, Voiceflow, Vapi) + a deterministic fixture adapter let
the chain be unit-exercised end to end against fixtures.
"""
from __future__ import annotations

import sys
from pathlib import Path

__version__ = "0.1.0"
HARNESS_VERSION = "e2e-harness@0.1.0"

# --- path bootstrap -------------------------------------------------------
# This tool straddles two packages that are not installed into a shared
# environment in CI: the repo-root scanner packages (``harness``, ``evals``,
# ``probes``, ``rubric``) and the ``post_bind_ingest`` package which lives
# under ``tools/post-bind-ingest/src``. Make both importable when this
# package is imported, so ``python -m e2e_harness.runbook`` and pytest both
# resolve the same modules the shipped scanner uses.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_POST_BIND_SRC = _REPO_ROOT / "tools" / "post-bind-ingest" / "src"

for _p in (_REPO_ROOT, _POST_BIND_SRC):
    _s = str(_p)
    if _p.exists() and _s not in sys.path:
        sys.path.insert(0, _s)

REPO_ROOT = _REPO_ROOT


def repo_root() -> Path:
    return _REPO_ROOT
