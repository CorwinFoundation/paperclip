from __future__ import annotations

from pathlib import Path
import tomllib


def test_runtime_dependencies_cover_package_initializer_imports() -> None:
    package_root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((package_root / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = metadata["project"]["dependencies"]
    assert any(dependency.startswith("pydantic") for dependency in dependencies), (
        "log_tracker.__init__ imports write_pipeline, which imports Pydantic v2; "
        "clean installations must declare that runtime dependency"
    )
