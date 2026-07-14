"""Static packaging and browser contract checks for the 203-case demo."""
from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


def test_browser_consumes_the_runnable_case_event_field() -> None:
    html = (REPO / "demo-real-agent" / "index.html").read_text(encoding="utf-8")

    assert "e.runnable_cases" in html
    assert "e.active_cases" not in html
    assert "h.runnable_case_count" in html
    assert "s.runnable_case_count" in html


def test_container_copies_every_materializer_data_root() -> None:
    dockerfile = (REPO / "demo-real-agent" / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY harness /app/harness" in dockerfile
    assert "COPY rubric /app/rubric" in dockerfile
    assert "COPY scanners /app/scanners" in dockerfile
    assert "COPY tests/probe_sets /app/tests/probe_sets" in dockerfile
    assert (
        "COPY artifacts/beaaa-152/datasets/halueval_dialogue_summ_general_n300.json "
        "/app/artifacts/beaaa-152/datasets/halueval_dialogue_summ_general_n300.json"
        in dockerfile
    )
    assert "COPY tools/text_case_materializer.py" in dockerfile


def test_release_archive_contains_every_docker_build_context_root() -> None:
    workflow = (REPO / ".github" / "workflows" / "real-agent-demo.yml").read_text(
        encoding="utf-8"
    )
    release_command = next(
        line.strip()
        for line in workflow.splitlines()
        if line.strip().startswith("tar -czf real-agent-demo-release.tgz")
    )

    for required_path in (
        "api",
        "harness",
        "rubric",
        "scanners",
        "tools",
        "demo-real-agent",
        "tests/probe_sets",
        "artifacts/beaaa-152/datasets/halueval_dialogue_summ_general_n300.json",
    ):
        assert required_path in release_command.split()
