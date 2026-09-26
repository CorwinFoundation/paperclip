"""No `pull_request`-triggered job may run on the self-hosted runner (BEAAA-36018).

Every workflow under `.github/workflows/` is parsed, and any job in a workflow
triggered by `pull_request` or `pull_request_target` must resolve to a
GitHub-hosted runner. The self-hosted runner is the box where production deploy
authority physically lives:

  * `.github/workflows/scanner-edge-image.yml` job `deploy` reads the pinned SSH
    identity `/home/ghrunner/.ssh/scanner_deploy_key`, scps the image to
    `are-deploy@srv1749248.hstgr.cloud`, and runs `sudo /usr/local/sbin/scanner-deploy`;
  * it also carries `secrets.PAPERCLIP_DEPLOY_AGENT_TOKEN`.

That job is `push` + `refs/heads/master` gated, so it only ever executes reviewed,
merged code. `pull_request` workflows are different: GitHub executes the workflow
file as it exists on the *PR head*, so a `pull_request` job on the self-hosted
runner would run unreviewed contributor code on the same machine as the deploy key.
Runner isolation (BEAAA-35606, recoverable on branch `beaaa-35606-runner-isolation`
at `0c9ee60c`) is the mitigation and it is **not landed**.

The guard is about the trigger, not about `self-hosted` being forbidden: the
push-gated `deploy` job stays green here. If you are retargeting the required
checks at the self-hosted runner for capacity reasons, land runner isolation
first, then relax this test deliberately.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"

PR_TRIGGERS = ("pull_request", "pull_request_target")

# GitHub-hosted runner images. Anything else is treated as self-hosted capacity:
# label-only routing (`runs-on: [my-box]`, no `self-hosted` label) reaches a
# self-hosted runner just as well as the explicit label does.
HOSTED_LABEL_RE = re.compile(r"^(?:ubuntu|macos|macOS|windows)-[\w.]+$")

EXPRESSION_RE = re.compile(r"\$\{\{(.+?)\}\}")
MATRIX_REF_RE = re.compile(r"^\s*matrix\.([A-Za-z_][\w-]*)\s*$")

WHY = (
    "The self-hosted runner holds the deploy SSH identity "
    "/home/ghrunner/.ssh/scanner_deploy_key and secrets.PAPERCLIP_DEPLOY_AGENT_TOKEN, "
    "and `pull_request` workflows execute the workflow file as it exists on the PR head — "
    "so this would run unreviewed code on the machine that holds production deploy "
    "authority. Runner isolation (BEAAA-35606, branch beaaa-35606-runner-isolation at "
    "0c9ee60c) is NOT landed. Land that first, then relax this guard deliberately. "
    "A push-gated job (see scanner-edge-image.yml `deploy`) may stay on the "
    "self-hosted runner; this guard is about the trigger."
)


def load_workflow(text: str) -> dict:
    """Parse workflow YAML without YAML 1.1 bool coercion (`on:` must stay `on`)."""
    return yaml.load(text, Loader=yaml.BaseLoader) or {}


def triggers(workflow: dict) -> list[str]:
    on = workflow.get("on", workflow.get(True))
    if isinstance(on, dict):
        return list(on)
    if isinstance(on, list):
        return [str(item) for item in on]
    if on is None:
        return []
    return [str(on)]


def is_pr_triggered(workflow: dict) -> bool:
    return any(t in PR_TRIGGERS for t in triggers(workflow))


def _matrix_values(job: dict, key: str) -> list[str] | None:
    """Every value `matrix.<key>` can take, or None when it cannot be resolved."""
    strategy = job.get("strategy")
    matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
    if not isinstance(matrix, dict):
        return None
    values: list[str] = []
    direct = matrix.get(key)
    if isinstance(direct, list):
        values.extend(str(v) for v in direct)
    elif isinstance(direct, str):
        # `matrix: {os: "${{ ... }}"}` — a dynamic axis we cannot resolve.
        return None
    include = matrix.get("include")
    if isinstance(include, list):
        for entry in include:
            if isinstance(entry, dict) and key in entry:
                values.append(str(entry[key]))
    return values or None


def resolve_labels(job: dict) -> tuple[list[str], list[str]]:
    """Return (resolved runner labels, unresolvable expressions) for one job.

    Handles the string, list, and `{group, labels}` forms of `runs-on`, and
    expands `${{ matrix.x }}` against the job's own matrix so a matrix-valued
    runner cannot slip past. Anything still containing an expression is reported
    as unresolvable and fails the guard: we cannot prove where it lands.
    """
    runs_on = job.get("runs-on")
    raw: list[str] = []
    if isinstance(runs_on, str):
        raw = [runs_on]
    elif isinstance(runs_on, list):
        raw = [str(item) for item in runs_on]
    elif isinstance(runs_on, dict):
        labels = runs_on.get("labels")
        if isinstance(labels, str):
            raw = [labels]
        elif isinstance(labels, list):
            raw = [str(item) for item in labels]
        group = runs_on.get("group")
        if isinstance(group, str):
            # A runner group is self-hosted capacity; carry it as a label so an
            # unknown group name is not silently accepted.
            raw.append(group)

    resolved: list[str] = []
    unresolved: list[str] = []
    for token in raw:
        if "${{" not in token:
            resolved.append(token)
            continue
        expanded = _expand(token, job)
        if expanded is None:
            unresolved.append(token)
        else:
            resolved.extend(expanded)
    return resolved, unresolved


def _expand(token: str, job: dict) -> list[str] | None:
    match = EXPRESSION_RE.search(token)
    if match is None:
        return [token]
    ref = MATRIX_REF_RE.match(match.group(1))
    if ref is None:
        return None
    values = _matrix_values(job, ref.group(1))
    if values is None:
        return None
    out: list[str] = []
    for value in values:
        substituted = token[: match.start()] + value + token[match.end() :]
        if "${{" in substituted:
            return None
        out.append(substituted)
    return out


def self_hosted_findings(workflow: dict) -> list[str]:
    """Jobs in a PR-triggered workflow whose runner is not provably GitHub-hosted."""
    if not is_pr_triggered(workflow):
        return []
    findings: list[str] = []
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        return []
    for name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        if "uses" in job and "runs-on" not in job:
            # Reusable-workflow call: the callee's own file is checked on its own.
            continue
        labels, unresolved = resolve_labels(job)
        if unresolved:
            findings.append(f"job {name!r}: runs-on is an unresolvable expression {unresolved!r}")
            continue
        if not labels:
            findings.append(f"job {name!r}: no runs-on")
            continue
        bad = [label for label in labels if not HOSTED_LABEL_RE.match(label)]
        if bad:
            findings.append(f"job {name!r}: runs-on {labels!r} (non-hosted label(s) {bad!r})")
    return findings


def workflow_files() -> list[Path]:
    return sorted(p for p in WORKFLOW_DIR.iterdir() if p.suffix in {".yml", ".yaml"})


def test_workflow_dir_is_readable() -> None:
    files = workflow_files()
    assert files, f"no workflow files found under {WORKFLOW_DIR}"


@pytest.mark.parametrize("path", workflow_files(), ids=lambda p: p.name)
def test_no_pull_request_job_runs_on_self_hosted(path: Path) -> None:
    workflow = load_workflow(path.read_text(encoding="utf-8"))
    findings = self_hosted_findings(workflow)
    assert not findings, (
        f"{path.relative_to(ROOT)} is triggered by "
        f"{[t for t in triggers(workflow) if t in PR_TRIGGERS]} and has job(s) that do not "
        f"resolve to a GitHub-hosted runner:\n  - " + "\n  - ".join(findings) + f"\n\n{WHY}"
    )


def test_deploy_job_is_self_hosted_and_push_gated() -> None:
    """The one self-hosted job on master, and why the guard does not touch it."""
    path = WORKFLOW_DIR / "scanner-edge-image.yml"
    workflow = load_workflow(path.read_text(encoding="utf-8"))
    assert not is_pr_triggered(workflow), (
        "scanner-edge-image.yml gained a pull_request trigger while its `deploy` job "
        f"is on the self-hosted runner. {WHY}"
    )
    deploy = workflow["jobs"]["deploy"]
    assert deploy["runs-on"] == ["self-hosted", "linux", "x64"]
    assert "github.event_name == 'push'" in deploy["if"]
    assert "refs/heads/master" in deploy["if"]


# --- the detector itself, so the guard cannot rot into a no-op -----------------

_PR_SELF_HOSTED = """
name: x
on:
  pull_request:
jobs:
  pytest:
    runs-on: [self-hosted, linux, x64]
"""

_PR_TARGET_BARE_LABEL = """
name: x
on:
  pull_request_target:
    branches: [master]
jobs:
  pytest:
    runs-on: scanner-box
"""

_PR_MATRIX_SELF_HOSTED = """
name: x
on: [pull_request]
jobs:
  pytest:
    strategy:
      matrix:
        include:
          - runner: ubuntu-latest
          - runner: self-hosted
    runs-on: ${{ matrix.runner }}
"""

_PR_GROUP = """
name: x
on:
  pull_request:
jobs:
  pytest:
    runs-on:
      group: scanner-edge
      labels: [x64]
"""

_PR_OPAQUE_EXPRESSION = """
name: x
on:
  pull_request:
jobs:
  pytest:
    runs-on: ${{ vars.RUNNER }}
"""

_PUSH_SELF_HOSTED = """
name: x
on:
  push:
    branches: [master]
jobs:
  deploy:
    runs-on: [self-hosted, linux, x64]
"""

_PR_HOSTED_MATRIX = """
name: x
on:
  pull_request:
  push:
jobs:
  pytest:
    strategy:
      matrix:
        os: [ubuntu-latest, ubuntu-22.04]
    runs-on: ${{ matrix.os }}
"""


@pytest.mark.parametrize(
    "source",
    [_PR_SELF_HOSTED, _PR_TARGET_BARE_LABEL, _PR_MATRIX_SELF_HOSTED, _PR_GROUP, _PR_OPAQUE_EXPRESSION],
)
def test_detector_flags_pr_triggered_non_hosted_runners(source: str) -> None:
    assert self_hosted_findings(load_workflow(source))


@pytest.mark.parametrize("source", [_PUSH_SELF_HOSTED, _PR_HOSTED_MATRIX])
def test_detector_passes_push_gated_and_hosted_runners(source: str) -> None:
    assert self_hosted_findings(load_workflow(source)) == []
