"""The new pool is opt-in, actor-bound, job-bound and queue-only after opt-in."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_pool_policy", ROOT / "scripts/ci/build_pool_policy.py")
policy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = policy
SPEC.loader.exec_module(policy)

TRUST = (
    "vars.BACKBOND_LOCAL_DOCKER == 'true' && github.repository == 'CorwinFoundation/are-scanner' && "
    "(github.actor == 'CorwinFoundation' || github.actor == 'backbond-qa') && "
    "(github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository)"
)


def route(key, role="build", suffix=""):
    labels = f'["self-hosted","linux","x64","backbond-{role}","bb-{{0}}-{{1}}-{key}"]'
    return "${{ fromJSON(" + TRUST + suffix + " && format('" + labels + "', github.run_id, github.run_attempt) || '[\"ubuntu-latest\"]') }}"


def sample(path="tests.yml", name="Docker and nginx integration coverage", branch="topic", event="pull_request"):
    run = dict(id=1234, run_attempt=2, path=f".github/workflows/{path}", event=event,
               head_branch=branch, head_sha="a" * 40,
               repository={"full_name": policy.REPOSITORY}, head_repository={"full_name": policy.REPOSITORY},
               actor={"login": "CorwinFoundation"}, triggering_actor={"login": "backbond-qa"})
    key, role, _ = policy.JOBS[run["path"]][name]
    job = dict(name=name, status="queued", labels=["self-hosted", "linux", "x64", f"backbond-{role}", f"bb-1234-2-{key}"])
    return run, job


def environment(expected):
    ref = "refs/heads/" + expected["branch"]
    if expected["branch"].startswith("are-collector-v0.1."):
        ref = "refs/tags/" + expected["branch"]
    return dict(GITHUB_REPOSITORY=policy.REPOSITORY, GITHUB_RUN_ID="1234", GITHUB_RUN_ATTEMPT="2",
                GITHUB_JOB=expected["job"], GITHUB_EVENT_NAME=expected["event"],
                GITHUB_ACTOR=expected["actor"], GITHUB_REF=ref,
                GITHUB_WORKFLOW_REF=f"{policy.REPOSITORY}/{expected['workflow']}@{ref}")


@pytest.mark.parametrize("path,name", [(path.removeprefix('.github/workflows/'), name)
                                      for path, jobs in policy.JOBS.items() for name in jobs])
def test_workflows_use_exact_opt_in_queue_only_role_and_job_labels(path, name):
    workflow = yaml.load((ROOT / ".github/workflows" / path).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    key, role, _ = policy.JOBS[f".github/workflows/{path}"][name]
    if path == "build-pool-smoke.yml":
        job = workflow["jobs"][key]
        assert workflow["on"] == {"workflow_dispatch": ""}
        assert job["runs-on"] == ["self-hosted", "linux", "x64", f"backbond-{role}",
                                 f"bb-${{{{ github.run_id }}}}-${{{{ github.run_attempt }}}}-{key}"]
        assert job["if"] == "${{ github.repository == 'CorwinFoundation/are-scanner' && github.ref == 'refs/heads/master' && (github.actor == 'CorwinFoundation' || github.actor == 'backbond-qa') }}"
        assert "packages: write" not in yaml.dump(job)
        return
    suffix = ""
    if path in {"scanner-image-readonly-verify.yml", "scanner-edge-image.yml"}:
        suffix = " && github.ref == 'refs/heads/master'"
    elif role == "publish":
        suffix = " && ((github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/master') || (github.event_name == 'push' && startsWith(github.ref, 'refs/tags/are-collector-v0.1.')))"
    assert workflow["jobs"][key]["runs-on"] == route(key, role, suffix)


@pytest.mark.parametrize("mutation", ["actor", "rerun_actor", "fork", "repository", "unknown_job", "unknown_workflow", "pr_target", "stale_attempt", "wrong_role", "generic_label", "extra_label", "in_progress", "empty_attempt", "bad_sha"])
def test_admission_rejects_untrusted_or_ambiguous_jobs(mutation):
    run, job = sample()
    if mutation == "actor": run["actor"]["login"] = "outsider"
    elif mutation == "rerun_actor": run["triggering_actor"]["login"] = "outsider"
    elif mutation == "fork": run["head_repository"]["full_name"] = "outsider/fork"
    elif mutation == "repository": run["repository"]["full_name"] = "outsider/fork"
    elif mutation == "unknown_job": job["name"] = "new job"
    elif mutation == "unknown_workflow": run["path"] = ".github/workflows/attacker.yml"
    elif mutation == "pr_target": run["event"] = "pull_request_target"
    elif mutation == "stale_attempt": run["run_attempt"] = 3
    elif mutation == "wrong_role": job["labels"][3] = "backbond-publish"
    elif mutation == "generic_label": job["labels"].pop()
    elif mutation == "extra_label": job["labels"].append("ariclaw")
    elif mutation == "in_progress": job["status"] = "in_progress"
    elif mutation == "bad_sha": run["head_sha"] = "unverified"
    else: run["run_attempt"] = None
    assert policy.admit(run, job) is None


@pytest.mark.parametrize("event,branch,accepted", [("push", "master", True), ("workflow_dispatch", "master", True),
    ("pull_request", "master", False), ("push", "topic", False), ("workflow_dispatch", "topic", False)])
def test_publisher_only_admits_reviewed_refs(event, branch, accepted):
    run, job = sample("scanner-edge-image.yml", "publish immutable scanner image and governed evidence", branch, event)
    expected = policy.admit(run, job)
    assert bool(expected) is accepted
    if expected:
        policy.check_environment(expected, environment(expected))


@pytest.mark.parametrize("key", ["GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB",
                                 "GITHUB_EVENT_NAME", "GITHUB_ACTOR", "GITHUB_WORKFLOW_REF", "GITHUB_REF"])
def test_immutable_hook_rejects_stolen_publisher_runner_labels(key):
    expected = policy.admit(*sample("scanner-edge-image.yml", "publish immutable scanner image and governed evidence", "master", "push"))
    env = environment(expected)
    env[key] = "attacker"
    with pytest.raises(ValueError):
        policy.check_environment(expected, env)


def test_each_trusted_actor_can_use_build_pool():
    for actor in sorted(policy.ACTORS):
        run, job = sample()
        run["actor"]["login"] = actor
        expected = policy.admit(run, job)
        assert expected
        policy.check_environment(expected, environment(expected))


def test_collector_tag_is_separate_from_manual_branch_and_historical_evidence():
    run, job = sample("are-collector-release.yml", "build-sign-sbom", "are-collector-v0.1.42", "push")
    expected = policy.admit(run, job)
    assert expected and expected["role"] == "publish"
    policy.check_environment(expected, environment(expected))
    run["event"] = "workflow_dispatch"
    assert policy.admit(run, job) is None
    assert not any("qa-evidence" in name or "sign-beaaa" in name for name in policy.JOBS)
