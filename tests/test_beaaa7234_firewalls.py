from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = ("pricing", "portal", "tools.post-bind-ingest", "bands")


def _python_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def test_money_movement_wrapper_has_no_downstream_pricing_imports() -> None:
    package = ROOT / "evals" / "perils" / "money_movement"
    bodies = "\n".join(path.read_text(encoding="utf-8") for path in _python_files(package))
    for forbidden in FORBIDDEN:
        assert f"import {forbidden}" not in bodies
        assert f"from {forbidden}" not in bodies


def test_stop_instruction_probe_has_no_downstream_pricing_imports() -> None:
    package = ROOT / "probes" / "stop_instruction" / "v1"
    bodies = "\n".join(path.read_text(encoding="utf-8") for path in _python_files(package))
    for forbidden in FORBIDDEN:
        assert f"import {forbidden}" not in bodies
        assert f"from {forbidden}" not in bodies

