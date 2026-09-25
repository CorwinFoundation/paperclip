from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.image_artifact_allowlist import (  # noqa: E402
    READ_CLASSIFICATIONS,
    RUNTIME_ARTIFACT_INVENTORY,
)


def runtime_artifact_inputs() -> tuple[str, ...]:
    """Every ``artifacts/`` path the registry generator may open at runtime.

    Derived from the one inventory the image allowlist audit reads, so an
    evidence file added there lands in this fixture without a second list to
    keep in step. A fixture repo missing one of these reproduces exactly the
    silent-empty-evidence defect BEAAA-35637 closed.
    """
    return tuple(
        sorted(
            relative
            for relative, classification in RUNTIME_ARTIFACT_INVENTORY.items()
            if classification in READ_CLASSIFICATIONS
        )
    )


def copy_registry_fixture(repo: Path, destination: Path) -> None:
    """Copy the bounded inputs needed to run the registry generator."""
    directories = (
        "config",
        "corpus",
        "evals",
        "harness",
        "rubric",
        "scanners",
        "tests/fixtures",
        "tests/probe_sets",
        "tools",
    )
    for relative in directories:
        source = repo / relative
        if source.exists():
            shutil.copytree(source, destination / relative)

    for relative in runtime_artifact_inputs():
        source = repo / relative
        if not source.exists():
            raise FileNotFoundError(
                f"{relative} is read at runtime per "
                f"tools/image_artifact_allowlist.py but is absent from {repo}; "
                "the registry fixture cannot stand in for the real repo without it"
            )
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
