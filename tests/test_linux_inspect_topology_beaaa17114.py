"""Regression guard for the isolated Linux/Inspect CI topology (BEAAA-17114)."""

from pathlib import Path


WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "tests.yml"
)
INSPECT_TESTS = (
    "tests/test_inspect_runner.py",
    "tests/unit/test_inspect_provenance_pin.py",
)


def test_core_excludes_and_inspect_job_executes_every_inspect_test() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    run_tests = workflow.split("- name: Run tests", 1)[1].split(
        "eval-framework-venvs:", 1
    )[0]
    inspect_step = workflow.split(
        "- name: Run Inspect-specific tests in pinned Inspect environment", 1
    )[1].split("- name: Report datasets resolver split", 1)[0]

    for test_path in INSPECT_TESTS:
        assert f"--ignore={test_path}" in run_tests
        assert test_path in inspect_step

    assert "if: matrix.framework == 'inspect'" in inspect_step
    assert '"$ARE_EVAL_VENVS_DIR/inspect-venv/bin/python" -m pytest' in inspect_step
    assert "locks/core-py312.lock" in workflow
    assert "locks/inspect-py312.lock" in workflow
