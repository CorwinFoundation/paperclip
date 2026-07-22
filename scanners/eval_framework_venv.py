"""Per-framework virtualenv resolver for offline eval framework workers."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_VENVS_ROOT = "/opt/are-scanner/eval-venvs"
_ENV_VAR = "ARE_EVAL_VENVS_DIR"
_PROBE_TIMEOUT_S = 60


@dataclass(frozen=True)
class FrameworkSpec:
    key: str
    import_name: str
    pinned_version: str
    requirements: str


FRAMEWORKS: dict[str, FrameworkSpec] = {
    "garak": FrameworkSpec("garak", "garak", "0.10.0", "requirements-eval-garak.txt"),
    "pyrit": FrameworkSpec("pyrit", "pyrit", "0.6.0", "requirements-eval-pyrit.txt"),
    "agentdojo": FrameworkSpec("agentdojo", "agentdojo", "0.1.35", "requirements-eval-agentdojo.txt"),
    "inspect": FrameworkSpec("inspect", "inspect_ai", "0.3.220", "requirements-eval-inspect.txt"),
}


class EvalVenvError(RuntimeError):
    pass


def _require_known(framework: str) -> FrameworkSpec:
    spec = FRAMEWORKS.get(framework)
    if spec is None:
        raise EvalVenvError(f"unknown eval framework {framework!r}; known: {sorted(FRAMEWORKS)}")
    return spec


def venvs_root() -> Path:
    return Path(os.environ.get(_ENV_VAR, DEFAULT_VENVS_ROOT))


def venv_dir(framework: str) -> Path:
    _require_known(framework)
    return venvs_root() / f"{framework}-venv"


def venv_python(framework: str) -> Path:
    vdir = venv_dir(framework)
    win = vdir / "Scripts" / "python.exe"
    if win.exists():
        return win
    return vdir / "bin" / "python"


def is_available(framework: str) -> bool:
    try:
        return venv_python(framework).exists()
    except EvalVenvError:
        return False


def resolve_framework(framework: str, *, timeout: int = _PROBE_TIMEOUT_S) -> tuple[bool, str]:
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
    return (True, proc.stdout.strip() or "unknown")


def run_in_venv(
    framework: str,
    args: list[str],
    *,
    timeout: int | None = None,
    **subprocess_kwargs,
) -> subprocess.CompletedProcess[str]:
    _require_known(framework)
    py = venv_python(framework)
    if not py.exists():
        raise EvalVenvError(
            f"{framework} venv not found at {venv_dir(framework)}; build it with "
            "`scripts/build_eval_framework_venvs.sh {framework}`."
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
    if module is not None:
        return (True, str(getattr(module, "__version__", "unknown")))
    return resolve_framework(framework)
