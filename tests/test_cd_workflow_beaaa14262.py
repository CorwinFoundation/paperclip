from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "cd.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _run_blocks(workflow: str) -> list[str]:
    blocks: list[str] = []
    lines = workflow.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        match = re.match(r"^(\s*)run:\s*\|", line)
        if not match:
            index += 1
            continue
        base_indent = len(match.group(1))
        block: list[str] = []
        index += 1
        while index < len(lines):
            child = lines[index]
            if child.strip() and len(child) - len(child.lstrip(" ")) <= base_indent:
                break
            block.append(child)
            index += 1
        blocks.append("\n".join(block))
    return blocks


def test_dispatch_inputs_are_not_interpolated_inside_run_scripts() -> None:
    workflow = _workflow()

    assert "${{ github.event.inputs.image_sha || github.sha }}" not in workflow
    assert all("github.event.inputs." not in block for block in _run_blocks(workflow))


def test_manual_image_sha_is_validated_in_unprivileged_prerequisite_job() -> None:
    workflow = _workflow()

    resolve_job = workflow.split("  resolve-image-sha:", maxsplit=1)[1].split("\n  build-push:", maxsplit=1)[0]
    assert "permissions:\n      contents: read" in resolve_job
    assert "id-token: write" not in resolve_job
    assert "packages: write" not in resolve_job
    assert "DISPATCH_IMAGE_SHA: ${{ github.event.inputs.image_sha }}" in resolve_job
    assert 'SHA="${DISPATCH_IMAGE_SHA:-$DEFAULT_IMAGE_SHA}"' in resolve_job
    assert r'[[ "$SHA" =~ ^[0-9a-f]{40,64}$ ]]' in resolve_job
    assert "exit 1" in resolve_job

    build_job = workflow.split("  build-push:", maxsplit=1)[1].split("\n  release-approval:", maxsplit=1)[0]
    assert "      - resolve-image-sha" in build_job
    assert "RESOLVED_IMAGE_SHA: ${{ needs.resolve-image-sha.outputs.sha }}" in build_job
    assert 'SHA="$RESOLVED_IMAGE_SHA"' in build_job
    assert "id-token: write" in build_job
    assert "packages: write" in build_job


def test_founder_gate_still_follows_build_push() -> None:
    workflow = _workflow()
    release_job = workflow.split("  release-approval:", maxsplit=1)[1]

    assert "needs: build-push" in release_job
    assert "environment: production" in release_job
