"""Per-framework virtualenv resolver for the conflicting offline eval frameworks.

BEAAA-13331 (non-blocking follow-up to ADR-0036 / BEAAA-7347). The full
eval-framework release contract in ``requirements.txt`` can never be installed
into one interpreter: ``garak==0.10.0`` requires ``datasets<2.17`` while
``pyrit==0.6.0`` requires ``datasets>=3.0.0`` (and the openai pins collided
before that). Offline calibration / CI still needs every framework available
so the garak / pyrit / agentdojo / inspect probe families can run.

Strategy: materialize each conflicting framework in its OWN venv, built by
``scripts/build_eval_framework_venvs.sh``, and let the soft-import runners
(``scanners/{pyrit,agentdojo,inspect}_runner.py``) resolve framework
availability + pinned version *from the matching per-framework venv, invoked as
a subprocess* instead of importing the framework into the harness interpreter
(where the combined graph is unsolvable). garak already runs out-of-process via
promptfoo, so this simply extends the same subprocess boundary to the other
three.

Design invariants (this module is wiring, NOT a behavior change):

* **Backward compatible.** The runners keep their in-process ``import`` as the
  primary resolution. This resolver is only consulted as a *fallback* when the
  in-process import fails. When no per-framework venv is configured or present,
  ``resolve_framework`` returns ``(False, "not-installed")`` — byte-identical
  to the pre-existing ``except ImportError`` branch. So existing runner unit
  tests and the N=5 golden calibration behave exactly as before.
* **Offline / CI only.** This substrate never touches the production runtime
  slice (BEAAA-13329). It changes only where the *availability + version stamp*
  comes from; the phase-1 runner execution path is untouched (prompts are
  pre-realized, ``runner.complete()`` drives the subject endpoint, and the
  shared ``harness.bench.*`` mappers score — no scoring / rubric / corpus
  change).
* **Wrap, don't fork.** No upstream internals are copied. The subprocess
  boundary is the supported integration surface, same as
  ``scanners/promptfoo_runner.py``.

Environment:

* ``ARE_EVAL_VENVS_DIR`` — root directory holding the per-framework venvs.
  Defaults to ``/opt/are-scanner/eval-venvs``. Each framework lives at
  ``<root>/<framework>-venv`` with a standard ``bin/python``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Default root for the per-framework venvs. Overridable via ARE_EVAL_VENVS_DIR
# so an offline calibration box / CI runner can point at wherever
# scripts/build_eval_framework_venvs.sh materialized them.
DEFAULT_VENVS_ROOT = "/opt/are-scanner/eval-venvs"
_ENV_VAR = "ARE_EVAL_VENVS_DIR"

# Default subprocess timeout (seconds) for the version-probe call. A cold
# import of a heavy framework (agentdojo pulls torch/transformers) can be slow,
# so keep this generous; the probe runs at most once per framework per process.
_PROBE_TIMEOUT_S = 60


@dataclass(frozen=True)
class FrameworkSpec:
    """Pinned coordinates for one offline eval framework.

    ``key`` is the ARE-internal short name used for the venv directory and the
    runner call sites. ``import_name`` is the Python module the venv exposes.
    ``pinned_version`` mirrors the ``requirements.txt`` release-contract pin so
    the build recipe and the resolver agree on the expected version.
    ``requirements`` is the per-framework requirements file the build script
    installs into the isolated venv.
    """

    key: str
    import_name: str
    pinned_version: str
    requirements: str


# The release contract, split into per-venv slices. These pins are the SAME
# exact versions as requirements.txt — this module does not bump anything (a
# bump is a rule-change requiring CTO co-sign + a fresh N=5 delta per
# contracts/scanner-implementation-supervision-v1.md).
FRAMEWORKS: dict[str, FrameworkSpec] = {
    "garak": FrameworkSpec(
        key="garak",
        import_name="garak",
        pinned_version="0.10.0",
        requirements="requirements-eval-garak.txt",
    ),
    "pyrit": FrameworkSpec(
        key="pyrit",
        import_name="pyrit",
        pinned_version="0.6.0",
        requirements="requirements-eval-pyrit.txt",
    ),
    "agentdojo": FrameworkSpec(
        key="agentdojo",
        import_name="agentdojo",
        pinned_version="0.1.35",
        requirements="requirements-eval-agentdojo.txt",
    ),
    "inspect": FrameworkSpec(
        key="inspect",
        import_name="inspect_ai",
        pinned_version="0.3.220",
        requirements="requirements-eval-inspect.txt",
    ),
}


class EvalVenvError(RuntimeError):
    """Raised when a per-framework venv is required but missing/broken."""


def venvs_root() -> Path:
    """Return the configured per-framework venv root directory."""
    return Path(os.environ.get(_ENV_VAR, DEFAULT_VENVS_ROOT))


def venv_dir(framework: str) -> Path:
    """Return the venv directory for ``framework`` (``<root>/<framework>-venv``)."""
    _require_known(framework)
    return venvs_root() / f"{framework}-venv"


def venv_python(framework: str) -> Path:
    """Return the interpreter path inside the framework's venv.

    Handles the POSIX (``bin/python``) and Windows (``Scripts/python.exe``)
    layouts; ARE runs on POSIX but the branch keeps the helper portable.
    """
    vdir = venv_dir(framework)
    win = vdir / "Scripts" / "python.exe"
    if win.exists():
        return win
    return vdir / "bin" / "python"


def _require_known(framework: str) -> FrameworkSpec:
    spec = FRAMEWORKS.get(framework)
    if spec is None:
        raise EvalVenvError(
            f"unknown eval framework {framework!r}; "
            f"known: {sorted(FRAMEWORKS)}"
        )
    return spec


def is_available(framework: str) -> bool:
    """True iff the framework's venv exists with a usable interpreter."""
    try:
        return venv_python(framework).exists()
    except EvalVenvError:
        return False


def resolve_framework(
    framework: str,
    *,
    timeout: int = _PROBE_TIMEOUT_S,
) -> tuple[bool, str]:
    """Resolve ``(available, version)`` from the per-framework venv.

    This is the fallback the runners call when their in-process ``import``
    fails. Contract:

    * If the venv / interpreter is absent → ``(False, "not-installed")``. This
      is the no-op path (unconfigured CI host) and is byte-identical to the
      runners' historical ``except ImportError`` branch, so nothing downstream
      changes.
    * If the venv is present, subprocess-probe it for
      ``<import_name>.__version__``. On success → ``(True, version)``. On any
      failure (broken venv, import error, timeout) → ``(False, "not-installed")``
      so a half-built venv degrades to "framework unavailable" rather than
      raising through the harness import path.

    No network, no scoring, deterministic given the on-disk venv.
    """
    spec = _require_known(framework)
    py = venv_python(framework)
    if not py.exists():
        return (False, "not-installed")
    code = (
        f"import {spec.import_name} as _m, sys; "
        f"sys.stdout.write(getattr(_m, '__version__', 'unknown'))"
    )
    try:
        proc = subprocess.run(
            [str(py), "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return (False, "not-installed")
    if proc.returncode != 0:
        return (False, "not-installed")
    version = proc.stdout.strip() or "unknown"
    return (True, version)


def run_in_venv(
    framework: str,
    args: list[str],
    *,
    timeout: int | None = None,
    **subprocess_kwargs,
) -> "subprocess.CompletedProcess[str]":
    """Invoke ``<venv-python> <args...>`` in the framework's venv.

    Generic subprocess entry point for a future phase-2 native driver (e.g. a
    real ``inspect_ai.eval()`` / PyRIT orchestrator call executed inside the
    isolated venv and returning JSON on stdout). Not used by the phase-1
    runners — they only need :func:`resolve_framework` — but documented and
    tested here so the boundary is ready without a second wiring pass.

    Raises :class:`EvalVenvError` if the venv is missing.
    """
    _require_known(framework)
    py = venv_python(framework)
    if not py.exists():
        raise EvalVenvError(
            f"{framework} venv not found at {venv_dir(framework)}; build it "
            f"with `scripts/build_eval_framework_venvs.sh {framework}`."
        )
    return subprocess.run(
        [str(py), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        **subprocess_kwargs,
    )


def resolve_or_import(framework: str, module: object | None) -> tuple[bool, str]:
    """Convenience for the runner soft-import blocks.

    ``module`` is the already-imported framework module (or ``None`` if the
    in-process import failed). If present, resolve from it in-process — the
    historical fast path, untouched. Otherwise fall back to the per-framework
    venv probe.
    """
    if module is not None:
        return (True, str(getattr(module, "__version__", "unknown")))
    return resolve_framework(framework)


__all__ = [
    "DEFAULT_VENVS_ROOT",
    "EvalVenvError",
    "FRAMEWORKS",
    "FrameworkSpec",
    "is_available",
    "resolve_framework",
    "resolve_or_import",
    "run_in_venv",
    "venv_dir",
    "venv_python",
    "venvs_root",
]


if __name__ == "__main__":  # pragma: no cover - operator convenience
    # `python -m scanners.eval_framework_venv` prints a resolution table so an
    # operator can confirm a build box has all four venvs materialized.
    print(f"{_ENV_VAR}={venvs_root()}")
    width = max(len(k) for k in FRAMEWORKS)
    ok = True
    for name in sorted(FRAMEWORKS):
        avail, ver = resolve_framework(name)
        expected = FRAMEWORKS[name].pinned_version
        status = "OK" if avail else "MISSING"
        match = "" if not avail else ("" if ver == expected else f"  !! expected {expected}")
        if not avail or ver != expected:
            ok = False
        print(f"  {name:<{width}}  {status:<8} {ver}{match}")
    sys.exit(0 if ok else 1)
