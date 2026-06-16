"""Shared filesystem paths for tests. Imported after conftest puts tests/ on sys.path."""

from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SRC = PACKAGE_ROOT / "src"
REPO_ROOT = PACKAGE_ROOT.parents[1]
