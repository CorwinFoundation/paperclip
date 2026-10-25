"""Founder-approved routing is a spending contract, separate from isolation.

BEAAA-40719: a queue-health fallback can preserve isolation and still violate
the founder's spending instruction. This guard rejects that exact failure mode.
Changes to this contract require Ari's explicit approval, not agent-only sign-off.
"""

from copy import deepcopy
import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
TRUSTED_ROUTE = (
    "vars.BACKBOND_LOCAL_CI == 'true' && "
    "(github.actor == 'CorwinFoundation' || github.actor == 'backbond-qa') && "
    "(github.event_name != 'pull_request' || "
    "github.event.pull_request.head.repo.full_name == github.repository) && "
    "'[\"self-hosted\",\"linux\",\"x64\",\"backbond-ci\"]' || '[\"ubuntu-latest\"]'"
)
QUEUED_SELECTOR = "${{ " + TRUSTED_ROUTE + " }}"
QUEUED_DIRECT = "${{ fromJSON(" + TRUSTED_ROUTE + ") }}"
QUEUED_JOB = "${{ fromJSON(needs.changes.outputs.runner || '[\"ubuntu-latest\"]') }}"
QUEUED_PREFLIGHT = "${{ fromJSON(" + TRUSTED_ROUTE.replace(
    "BACKBOND_LOCAL_CI", "BACKBOND_LOCAL_PREFLIGHT"
) + ") }}"
BUILD_TRUST = (
    "vars.BACKBOND_LOCAL_DOCKER == 'true' && github.repository == 'CorwinFoundation/are-scanner' && "
    "(github.actor == 'CorwinFoundation' || github.actor == 'backbond-qa') && "
    "(github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository)"
)
DOCKER_ROUTES = {
    key: "${{ fromJSON(" + BUILD_TRUST + " && format('"
         + '["self-hosted","linux","x64","backbond-build","bb-{0}-{1}-' + key
         + '\"]' + "', github.run_id, github.run_attempt) || '[\"ubuntu-latest\"]') }}"
    for key in ("native-integrations", "kaltura-browser-image")
}
DIRECT_TEST_JOBS = {"changes", "pytest-aggregate"}
MIGRATED_JOBS = {
    "pr-unit-tests.yml": {
        "changes", "brain-unit-tests", "portal-unit-tests", "probe-sdk-unit-tests", "connector-unit-tests",
        "are-collector-unit-tests", "runtime-slice-import-closure",
    },
    "log-listener.yml": {"test"},
    "beaaa-33964-ledger-drift.yml": {"ledger-drift"},
    "scanner-edge-image.yml": {"changes"},
    "weekly-branch-hygiene.yml": {"report"},
}


def workflows():
    def read(name):
        return yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)
    return read("tests.yml"), read("version-control-preflight.yml")


def normalized(value):
    return " ".join(str(value).split())


def cost_policy_findings(tests, preflight):
    findings = []
    if normalized(tests["jobs"]["changes"]["outputs"]["runner"]) != normalized(QUEUED_SELECTOR):
        findings.append("trusted tests must queue without load/age-based paid overflow")
    for name, job in tests["jobs"].items():
        expected = DOCKER_ROUTES.get(name, QUEUED_DIRECT if name in DIRECT_TEST_JOBS else QUEUED_JOB)
        if normalized(job.get("runs-on")) != normalized(expected):
            findings.append(f"test job {name} expands paid routing")
        if name not in DIRECT_TEST_JOBS and "needs.changes.result == 'success'" not in job.get("if", ""):
            findings.append(f"test job {name} may buy execution after selector failure")
        if name not in DIRECT_TEST_JOBS and "needs.changes.outputs.runner != ''" not in job.get("if", ""):
            findings.append(f"test job {name} may buy execution with missing runner outputs")
    aggregate = tests["jobs"]["pytest-aggregate"]["steps"][0]
    if (aggregate.get("env", {}).get("SELECTOR_RESULT") != "${{ needs.changes.result }}"
            or 'test "$SELECTOR_RESULT" = success' not in aggregate.get("run", "")):
        findings.append("selector failure must fail the required pytest aggregate")
    if (aggregate.get("env", {}).get("SELECTOR_RUNNER") != "${{ needs.changes.outputs.runner }}"
            or 'test -n "$SELECTOR_RUNNER"' not in aggregate.get("run", "")):
        findings.append("missing runner outputs must fail the required pytest aggregate")
    for name in ("release-identity", "policy"):
        if normalized(preflight["jobs"][name].get("runs-on")) != normalized(QUEUED_PREFLIGHT):
            findings.append(f"preflight {name} must queue on dedicated capacity")
    return findings


def test_workflows_keep_founder_approved_queue_only_policy():
    assert cost_policy_findings(*workflows()) == []


@pytest.mark.parametrize("filename,names", MIGRATED_JOBS.items())
def test_additional_jobs_queue_without_paid_overflow(filename, names):
    workflow = yaml.load((ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"),
                         Loader=yaml.BaseLoader)
    for name in names:
        job = workflow["jobs"][name]
        assert normalized(job["runs-on"]) == normalized(QUEUED_DIRECT), (filename, name)
        permissions = job.get("permissions", workflow.get("permissions"))
        assert permissions and all(value == "read" for value in permissions.values())
        assert "secrets." not in yaml.dump(job)


@pytest.mark.skipif(os.name == "nt", reason="Executes the Linux runner's aggregate shell")
@pytest.mark.parametrize("selector,runner,shards,native,image,expected", [
    ("success", "configured", "success", "success", "success", 0),
    ("success", "configured", "skipped", "skipped", "success", 0),
    ("failure", "configured", "success", "success", "success", 1),
    ("failure", "configured", "skipped", "skipped", "skipped", 1),
    ("skipped", "configured", "skipped", "skipped", "skipped", 1),
    ("", "configured", "success", "success", "success", 1),
    ("success", "", "success", "success", "success", 1),
    ("success", "", "skipped", "skipped", "success", 1),
])
def test_required_aggregate_fails_closed_on_selector_failure(selector, runner, shards, native, image, expected):
    aggregate = workflows()[0]["jobs"]["pytest-aggregate"]["steps"][0]
    result = subprocess.run(["bash", "-e", "-c", aggregate["run"]], capture_output=True,
                            text=True, env={**os.environ, "SELECTOR_RESULT": selector,
                                           "SELECTOR_RUNNER": runner,
                                           "SHARDS_RESULT": shards, "NATIVE_RESULT": native,
                                           "KALTURA_IMAGE_RESULT": image})
    assert result.returncode == expected, result.stdout + result.stderr


@pytest.mark.parametrize("mutation", ["busy_fallback", "exclude_qa", "hosted_preflight", "hosted_pytest", "hosted_selector", "hosted_aggregate", "hosted_docker", "docker_busy_fallback", "selector_failure_fallback", "missing_selector_gate", "empty_runner_fallback", "missing_runner_gate", "extra_hosted_job"])
def test_cost_guard_rejects_spending_regressions_even_when_isolation_is_preserved(mutation):
    tests, preflight = deepcopy(workflows())
    if mutation == "busy_fallback":
        # The exact PR #1135 policy: still isolated when selected, but silently
        # buying hosted execution when the VM has a backlog.
        tests["jobs"]["changes"]["outputs"]["runner"] = (
            "${{ steps.pool.outputs.use_hosted != 'true' && " + TRUSTED_ROUTE + " }}"
        )
    elif mutation == "exclude_qa":
        tests["jobs"]["changes"]["outputs"]["runner"] = QUEUED_SELECTOR.replace(
            "(github.actor == 'CorwinFoundation' || github.actor == 'backbond-qa')",
            "github.actor == 'CorwinFoundation'",
        )
    elif mutation == "hosted_preflight":
        preflight["jobs"]["policy"]["runs-on"] = "ubuntu-latest"
    elif mutation == "hosted_pytest":
        tests["jobs"]["pytest"]["runs-on"] = "ubuntu-latest"
    elif mutation == "hosted_selector":
        tests["jobs"]["changes"]["runs-on"] = "ubuntu-latest"
    elif mutation == "hosted_aggregate":
        tests["jobs"]["pytest-aggregate"]["runs-on"] = "ubuntu-latest"
    elif mutation == "hosted_docker":
        tests["jobs"]["native-integrations"]["runs-on"] = "ubuntu-latest"
    elif mutation == "docker_busy_fallback":
        tests["jobs"]["kaltura-browser-image"]["runs-on"] = DOCKER_ROUTES["kaltura-browser-image"].replace(
            BUILD_TRUST, "steps.capacity.outputs.busy != 'true' && " + BUILD_TRUST)
    elif mutation == "selector_failure_fallback":
        tests["jobs"]["pytest"]["if"] = "${{ !cancelled() }}"
    elif mutation == "missing_selector_gate":
        tests["jobs"]["pytest-aggregate"]["steps"][0]["env"].pop("SELECTOR_RESULT")
    elif mutation == "empty_runner_fallback":
        tests["jobs"]["pytest"]["if"] = tests["jobs"]["pytest"]["if"].replace(
            "needs.changes.outputs.runner != '' && ", "")
    elif mutation == "missing_runner_gate":
        tests["jobs"]["pytest-aggregate"]["steps"][0]["env"].pop("SELECTOR_RUNNER")
    else:
        tests["jobs"]["paid-overflow"] = {"runs-on": "ubuntu-latest"}
    assert cost_policy_findings(tests, preflight), mutation
