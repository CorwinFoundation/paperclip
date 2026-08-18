from __future__ import annotations

import shutil
from pathlib import Path


def copy_registry_fixture(repo: Path, destination: Path) -> None:
    """Copy the bounded inputs needed to run the registry generator."""
    directories = (
        "artifacts/beaaa-152",
        "artifacts/beaaa-18720",
        "artifacts/beaaa-21704",
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
