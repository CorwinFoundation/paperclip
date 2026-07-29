"""Static packaging and browser contract checks for the 203-case demo."""
from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
HALUEVAL_DATASET = (
    "artifacts/beaaa-152/datasets/halueval_dialogue_summ_general_n300.json"
)
CONTENT_CANDIDATE_INVENTORY = "artifacts/beaaa-18720/unified-probe-inventory.jsonl"


def test_browser_consumes_the_runnable_case_event_field() -> None:
    html = (REPO / "demo-real-agent" / "index.html").read_text(encoding="utf-8")

    assert "e.runnable_cases" in html
    assert "e.active_cases" not in html
    assert "h.runnable_case_count" in html
    assert "s.runnable_case_count" in html


def test_container_copies_every_materializer_data_root() -> None:
    dockerfile = (REPO / "demo-real-agent" / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (REPO / ".dockerignore").read_text(encoding="utf-8")

    assert "COPY harness /app/harness" in dockerfile
    assert "COPY rubric /app/rubric" in dockerfile
    assert "COPY scanners /app/scanners" in dockerfile
    assert "COPY release /app/release" in dockerfile
    assert "COPY release_identity.py /app/release_identity.py" in dockerfile
    assert "COPY tests/probe_sets /app/tests/probe_sets" in dockerfile
    assert (
        f"COPY {HALUEVAL_DATASET} /app/{HALUEVAL_DATASET}"
        in dockerfile
    )
    assert (
        f"COPY {CONTENT_CANDIDATE_INVENTORY} /app/{CONTENT_CANDIDATE_INVENTORY}"
        in dockerfile
    )
    assert "COPY tools/content_candidate_inventory.py" in dockerfile
    assert "COPY tools/text_case_materializer.py" in dockerfile
    assert "artifacts/*" in dockerignore
    assert f"!{HALUEVAL_DATASET}" in dockerignore
    assert f"!{CONTENT_CANDIDATE_INVENTORY}" in dockerignore


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
        "release",
        "release_identity.py",
        "tools",
        "demo-real-agent",
        "tests/probe_sets",
        HALUEVAL_DATASET,
        CONTENT_CANDIDATE_INVENTORY,
    ):
        assert required_path in release_command.split()


def test_ci_container_supplies_strong_demo_access_token() -> None:
    workflow = (REPO / ".github" / "workflows" / "real-agent-demo.yml").read_text(
        encoding="utf-8"
    )

    assert "-e DEMO_ACCESS_TOKEN=ci-only-real-agent-demo-access-token" in workflow
