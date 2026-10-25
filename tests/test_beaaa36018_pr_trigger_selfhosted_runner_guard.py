"""PR jobs must not reach the production deploy runner (BEAAA-36018).

Every workflow under `.github/workflows/` is parsed, and any job in a workflow
triggered by `pull_request` or `pull_request_target` must resolve to hosted or
explicitly isolated test capacity. The existing deploy runner is the box where production deploy
authority physically lives:

  * `.github/workflows/scanner-edge-image.yml` job `deploy` reads the pinned SSH
    identity `/home/ghrunner/.ssh/scanner_deploy_key`, scps the image to
    `are-deploy@srv1749248.hstgr.cloud`, and runs `sudo /usr/local/sbin/scanner-deploy`;
  * it also carries `secrets.PAPERCLIP_DEPLOY_AGENT_TOKEN`.

That job is `push` + `refs/heads/master` gated, so it only ever executes reviewed,
merged code. `pull_request` workflows are different: GitHub executes the workflow
file as it exists on the *PR head*, so a `pull_request` job on the self-hosted
runner would run unreviewed contributor code on the same machine as the deploy key.
The dedicated backbond-ci runner is a separate rootless container on another
host, without deploy credentials, host mounts, or host loopback access. Its
verified setup is documented in docs/dedicated-ci-runner.md.

The guard is about the trigger, not about `self-hosted` being forbidden: the
push-gated `deploy` job stays green here. If you are retargeting the required
checks at the self-hosted runner for capacity reasons, land runner isolation
first. The exception below recognizes only the trusted-actor/same-repository opt-in
route to that dedicated label; arbitrary self-hosted routes remain forbidden.
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
    "authority. Only the explicitly guarded route to the isolated backbond-ci "
    "runner is accepted; unguarded labels and opaque expressions remain forbidden. "
    "A push-gated job (see scanner-edge-image.yml `deploy`) may stay on the "
    "self-hosted runner; this guard is about the trigger."
)

DEDICATED_ELIGIBILITY = (
    "${{ vars.BACKBOND_LOCAL_CI == 'true' && "
    "(github.actor == 'CorwinFoundation' || github.actor == 'backbond-qa') && "
    "(github.event_name != 'pull_request' || "
    "github.event.pull_request.head.repo.full_name == github.repository) && "
    "'[\"self-hosted\",\"linux\",\"x64\",\"backbond-ci\"]' || '[\"ubuntu-latest\"]' }}"
)
# Eligible jobs wait for dedicated capacity rather than buying hosted overflow.
DEDICATED_OUTPUT = DEDICATED_ELIGIBILITY
DEDICATED_DIRECT = "${{ fromJSON(" + DEDICATED_ELIGIBILITY[4:-3] + ") }}"
DEDICATED_RUNS_ON = "${{ fromJSON(needs.changes.outputs.runner || '[\"ubuntu-latest\"]') }}"
DEDICATED_PREFLIGHT = "${{ fromJSON(" + DEDICATED_ELIGIBILITY[4:-3].replace(
    "BACKBOND_LOCAL_CI", "BACKBOND_LOCAL_PREFLIGHT"
) + ") }}"
CONTROL_DIRECT = DEDICATED_DIRECT.replace('"backbond-ci"', '"backbond-ci-control"')
CONTROL_PREFLIGHT = DEDICATED_PREFLIGHT.replace('"backbond-ci"', '"backbond-ci-control"')
CONTROL_ONLY = (
    "${{ vars.BACKBOND_LOCAL_CI == 'true' && "
    "(github.actor == 'CorwinFoundation' || github.actor == 'backbond-qa') && "
    "(github.event_name != 'pull_request' || "
    "github.event.pull_request.head.repo.full_name == github.repository) }}"
)
BUILD_TRUST = (
    "vars.BACKBOND_LOCAL_DOCKER == 'true' && github.repository == 'CorwinFoundation/are-scanner' && "
    "(github.actor == 'CorwinFoundation' || github.actor == 'backbond-qa') && "
    "(github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository)"
)
BUILD_ROUTES = {
    "${{ fromJSON(" + BUILD_TRUST + " && format('"
    + '["self-hosted","linux","x64","backbond-build","bb-{0}-{1}-' + key
    + '\"]' + "', github.run_id, github.run_attempt) || '[\"ubuntu-latest\"]') }}"
    for key in ("native-integrations", "kaltura-browser-image", "acceptance-matrix")
}


def is_isolated_route(job: dict, workflow: dict) -> bool:
    """Accept only the reviewed dedicated route, with no secrets or write token."""
    if "pull_request_target" in triggers(workflow):
        return False
    if "secrets." in yaml.dump(job):
        return False
    permissions = job.get("permissions", workflow.get("permissions"))
    if not isinstance(permissions, dict) or any(v != "read" for v in permissions.values()):
        return False
    if job.get("runs-on") == ["self-hosted", "linux", "x64", "backbond-ci-control"]:
        return job.get("if") == CONTROL_ONLY
    # A distinct UID and one-job JIT identity, private Docker daemon and fresh
    # disk. The broker also binds workflow, run/attempt and job before execution.
    if job.get("runs-on") in BUILD_ROUTES:
        return True
    if job.get("runs-on") in (DEDICATED_PREFLIGHT, DEDICATED_DIRECT, CONTROL_DIRECT, CONTROL_PREFLIGHT):
        return True
    if job.get("runs-on") != DEDICATED_RUNS_ON:
        return False
    needs = job.get("needs")
    if needs != "changes" and (not isinstance(needs, list) or "changes" not in needs):
        return False
    selector = workflow.get("jobs", {}).get("changes", {})
    return (
        selector.get("runs-on") in ("ubuntu-latest", DEDICATED_DIRECT, CONTROL_DIRECT)
        and selector.get("outputs", {}).get("runner") == DEDICATED_OUTPUT
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
    """PR jobs whose runner is neither hosted nor the guarded isolated route."""
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
        if is_isolated_route(job, workflow):
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
    """The production deploy runner must remain push-gated."""
    path = WORKFLOW_DIR / "scanner-edge-image.yml"
    workflow = load_workflow(path.read_text(encoding="utf-8"))
    assert not is_pr_triggered(workflow), (
        "scanner-edge-image.yml gained a pull_request trigger while its `deploy` job "
        f"is on the self-hosted runner. {WHY}"
    )
    deploy = workflow["jobs"]["deploy"]
    assert deploy["runs-on"] == ["self-hosted", "linux", "x64", "ariclaw"]
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


@pytest.mark.parametrize("mutation", [
    "owner", "qa_actor", "fork", "deploy_label", "opaque", "secret", "write", "pr_target",
    "selector_runner", "missing_needs",
])
def test_dedicated_route_still_rejects_unsafe_changes(mutation: str) -> None:
    workflow = load_workflow((WORKFLOW_DIR / "tests.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["pytest"]
    selector = workflow["jobs"]["changes"]
    if mutation == "owner":
        selector["outputs"]["runner"] = DEDICATED_OUTPUT.replace("CorwinFoundation", "someone")
    elif mutation == "qa_actor":
        selector["outputs"]["runner"] = DEDICATED_OUTPUT.replace("backbond-qa", "someone")
    elif mutation == "fork":
        selector["outputs"]["runner"] = DEDICATED_OUTPUT.replace(
            "github.event.pull_request.head.repo.full_name == github.repository", "true"
        )
    elif mutation == "deploy_label":
        selector["outputs"]["runner"] = DEDICATED_OUTPUT.replace("backbond-ci", "ariclaw")
    elif mutation == "opaque":
        job["runs-on"] = "${{ vars.RUNNER }}"
    elif mutation == "secret":
        job["env"]["DEPLOY_TOKEN"] = "${{ secrets.PAPERCLIP_DEPLOY_AGENT_TOKEN }}"
    elif mutation == "write":
        job["permissions"] = {"contents": "write"}
    elif mutation == "pr_target":
        workflow["on"]["pull_request_target"] = {}
    elif mutation == "selector_runner":
        selector["runs-on"] = "ariclaw"
    elif mutation == "missing_needs":
        job.pop("needs")
    assert self_hosted_findings(workflow), mutation


def test_guarded_preflight_route_is_accepted_but_deploy_label_is_rejected() -> None:
    workflow = {"on": {"pull_request": {}}, "permissions": {"contents": "read"},
                "jobs": {"preflight": {"runs-on": DEDICATED_PREFLIGHT}}}
    assert self_hosted_findings(workflow) == []
    workflow["jobs"]["preflight"]["runs-on"] = DEDICATED_PREFLIGHT.replace("backbond-ci", "ariclaw")
    assert self_hosted_findings(workflow)


@pytest.mark.parametrize("mutation", [
    "owner", "qa_actor", "fork", "deploy_label", "secret", "write", "pr_target",
    "busy_fallback",
])
def test_direct_dedicated_route_rejects_unsafe_changes(mutation: str) -> None:
    job = {"runs-on": DEDICATED_DIRECT, "steps": [{"run": "true"}]}
    workflow = {"on": {"pull_request": {}}, "permissions": {"contents": "read"},
                "jobs": {"selector": job}}
    assert self_hosted_findings(workflow) == []
    replacements = {
        "owner": ("CorwinFoundation", "someone"),
        "qa_actor": ("backbond-qa", "someone"),
        "fork": ("github.event.pull_request.head.repo.full_name == github.repository", "true"),
        "deploy_label": ("backbond-ci", "ariclaw"),
        "busy_fallback": ("vars.BACKBOND_LOCAL_CI", "!runner.busy && vars.BACKBOND_LOCAL_CI"),
    }
    if mutation in replacements:
        job["runs-on"] = DEDICATED_DIRECT.replace(*replacements[mutation])
    elif mutation == "secret":
        job["env"] = {"TOKEN": "${{ secrets.DEPLOY_TOKEN }}"}
    elif mutation == "write":
        job["permissions"] = {"contents": "write"}
    elif mutation == "pr_target":
        workflow["on"]["pull_request_target"] = {}
    assert self_hosted_findings(workflow), mutation


def test_trusted_jobs_queue_without_paid_busy_overflow() -> None:
    workflow = load_workflow((WORKFLOW_DIR / "tests.yml").read_text(encoding="utf-8"))
    selector = workflow["jobs"]["changes"]
    assert selector["outputs"]["runner"] == DEDICATED_ELIGIBILITY
    assert all(step.get("id") != "pool" for step in selector["steps"])
    assert "actions" not in selector["permissions"]
