from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "weekly-branch-hygiene.yml"


def _run_report(tmp_path: Path, branch_result: str, open_head: str) -> subprocess.CompletedProcess[str]:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    script = workflow["jobs"]["report"]["steps"][0]["run"]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
args="$*"
if [[ "$args" == *"pulls?state=open"* ]]; then
  printf '%s\\n' "${OPEN_HEAD}"
elif [[ "$args" == *"pulls?state=closed"* ]]; then
  printf 'agent/beaaa-21591-branch-hygiene\\t%s\\t191\\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
elif [[ "$args" == *"/branches/"* ]]; then
  case "${BRANCH_RESULT}" in
    present) printf 'HTTP/2 200 OK\\n' ;;
    absent) printf 'gh: Branch not found (HTTP 404)\\n' >&2; exit 1 ;;
    forbidden) printf 'gh: API rate limit exceeded (HTTP 403)\\n' >&2; exit 1 ;;
  esac
else
  exit 2
fi
"""
    )
    gh.chmod(0o755)
    summary = tmp_path / "summary.md"
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GITHUB_REPOSITORY": "CorwinFoundation/are-scanner",
        "GITHUB_STEP_SUMMARY": str(summary),
        "BRANCH_RESULT": branch_result,
        "OPEN_HEAD": open_head,
    }
    result = subprocess.run(["bash", "-c", script], env=env, text=True, capture_output=True)
    result.summary = summary.read_text()  # type: ignore[attr-defined]
    return result


def test_non_404_branch_lookup_is_incomplete_without_false_clean(tmp_path: Path) -> None:
    result = _run_report(tmp_path, "forbidden", "agent/beaaa-21598-fix")

    assert result.returncode == 0
    assert "Branch hygiene evaluation incomplete" in result.stdout
    assert "evaluation failed: branch collection was incomplete" in result.summary
    assert "Branch hygiene violations: 0" not in result.summary


def test_authoritative_404_is_clean(tmp_path: Path) -> None:
    result = _run_report(tmp_path, "absent", "agent/beaaa-21598-fix")

    assert result.returncode == 0
    assert "Branch hygiene evaluation incomplete" not in result.stdout
    assert result.summary == "Branch hygiene violations: 0\n"


def test_controlled_naming_and_retention_violations(tmp_path: Path) -> None:
    result = _run_report(tmp_path, "present", "bad-branch")

    assert result.returncode == 0
    assert "Branch naming violation" in result.stdout
    assert "Merged branch retained" in result.stdout
    assert result.summary == "Branch hygiene violations: 2\n"


def test_workflow_remains_schedule_only_read_only_and_report_only() -> None:
    text = WORKFLOW.read_text()
    workflow = yaml.safe_load(text)
    triggers = workflow.get("on", workflow.get(True))

    assert set(triggers) == {"schedule"}
    assert workflow["permissions"] == {"contents": "read", "pull-requests": "read"}
    for forbidden in ("workflow_dispatch:", "pull_request:", "push:", "gh pr merge", "gh api --method DELETE", "git push"):
        assert forbidden not in text
