"""Admission rules shared by the root broker and the immutable job-start hook.

No credentials, network calls or host mutations belong in this module.
"""
from __future__ import annotations
import re

REPOSITORY = "CorwinFoundation/are-scanner"
ACTORS = {"CorwinFoundation", "backbond-qa"}
# API job display name -> (job key, isolated account, wall timeout in minutes).
JOBS = {
    ".github/workflows/build-pool-smoke.yml": {
        "disposable Docker acceptance": ("docker-smoke", "build", 20),
        "disposable publishing acceptance": ("publish-smoke", "publish", 20),
    },
    ".github/workflows/tests.yml": {
        "Docker and nginx integration coverage": ("native-integrations", "build", 20),
        "Kaltura browser image readiness": ("kaltura-browser-image", "build", 30),
    },
    ".github/workflows/ci-plugin.yml": {
        "ci-plugin acceptance matrix": ("acceptance-matrix", "build", 25),
    },
    ".github/workflows/scanner-image-readonly-verify.yml": {
        "verify immutable Scanner image": ("verify", "build", 20),
    },
    ".github/workflows/scanner-edge-image.yml": {
        "publish immutable scanner image and governed evidence": ("image-build", "publish", 40),
    },
    ".github/workflows/are-collector-release.yml": {
        "build-sign-sbom": ("build-sign-sbom", "publish", 45),
    },
}


def admit(run: dict, job: dict) -> dict | None:
    """Fail closed on unknown jobs, forks, rerun actors and publishing refs."""
    path = run.get("path", "").split("@", 1)[0]
    spec = JOBS.get(path, {}).get(job.get("name"))
    if not spec or job.get("status") != "queued":
        return None
    if run.get("repository", {}).get("full_name") != REPOSITORY:
        return None
    actor = run.get("actor", {}).get("login")
    trigger = run.get("triggering_actor", {}).get("login")
    if actor not in ACTORS or trigger not in ACTORS:
        return None
    if run.get("head_repository", {}).get("full_name") != REPOSITORY:
        return None
    if not re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha", "")):
        return None
    event, branch = run.get("event"), run.get("head_branch", "")
    key, role, minutes = spec
    if role == "publish":
        allowed = event in {"push", "workflow_dispatch"} and branch == "master"
        if path.endswith("are-collector-release.yml"):
            allowed |= event == "push" and branch.startswith("are-collector-v0.1.")
        if not allowed:
            return None
    elif path.endswith(("scanner-image-readonly-verify.yml", "build-pool-smoke.yml")):
        if event != "workflow_dispatch" or branch != "master":
            return None
    elif event not in {"pull_request", "push", "workflow_dispatch"}:
        return None
    run_id, attempt = run.get("id"), run.get("run_attempt")
    if type(run_id) is not int or type(attempt) is not int or min(run_id, attempt) < 1:
        return None
    label = f"bb-{run_id}-{attempt}-{key}"
    expected_labels = {"self-hosted", "linux", "x64", f"backbond-{role}", label}
    if {str(x).lower() for x in job.get("labels", [])} != expected_labels:
        return None
    return dict(run_id=run_id, attempt=attempt, job=key, role=role, minutes=minutes,
                workflow=path, event=event, actor=actor, branch=branch,
                head_sha=run["head_sha"], label=label)


def check_environment(expected: dict, env: dict) -> None:
    """A generic runner label alone never authorizes a job to start."""
    fields = {
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_RUN_ID": str(expected["run_id"]),
        "GITHUB_RUN_ATTEMPT": str(expected["attempt"]),
        "GITHUB_JOB": expected["job"],
        "GITHUB_EVENT_NAME": expected["event"],
        "GITHUB_ACTOR": expected["actor"],
    }
    for key, value in fields.items():
        if env.get(key) != value:
            raise ValueError(f"job admission mismatch: {key}")
    workflow_ref = env.get("GITHUB_WORKFLOW_REF", "")
    prefix = f"{REPOSITORY}/{expected['workflow']}@"
    if not workflow_ref.startswith(prefix):
        raise ValueError("job admission mismatch: workflow")
    if expected["role"] == "publish":
        ref = "refs/heads/master"
        if expected["branch"].startswith("are-collector-v0.1."):
            ref = f"refs/tags/{expected['branch']}"
        if env.get("GITHUB_REF") != ref or workflow_ref != prefix + ref:
            raise ValueError("publishing requires the admitted master or release-tag workflow")
