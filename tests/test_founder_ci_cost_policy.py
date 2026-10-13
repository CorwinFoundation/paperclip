"""Founder-approved routing is a spending contract, separate from isolation.

BEAAA-40719: a queue-health fallback can preserve isolation and still violate
the founder's spending instruction. This guard rejects that exact failure mode.
Changes to this contract require Ari's explicit approval, not agent-only sign-off.
"""

from copy import deepcopy
from pathlib import Path

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
QUEUED_JOB = "${{ fromJSON(needs.changes.outputs.runner || '[\"ubuntu-latest\"]') }}"
QUEUED_PREFLIGHT = "${{ fromJSON(" + TRUSTED_ROUTE.replace(
    "BACKBOND_LOCAL_CI", "BACKBOND_LOCAL_PREFLIGHT"
) + ") }}"
HOSTED_TEST_BASELINE = {"changes", "native-integrations", "kaltura-browser-image", "pytest-aggregate"}


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
        if name not in HOSTED_TEST_BASELINE and normalized(job.get("runs-on")) != normalized(QUEUED_JOB):
            findings.append(f"test job {name} expands paid routing")
    for name in ("release-identity", "policy"):
        if normalized(preflight["jobs"][name].get("runs-on")) != normalized(QUEUED_PREFLIGHT):
            findings.append(f"preflight {name} must queue on dedicated capacity")
    return findings


def test_workflows_keep_founder_approved_queue_only_policy():
    assert cost_policy_findings(*workflows()) == []


@pytest.mark.parametrize("mutation", ["busy_fallback", "exclude_qa", "hosted_preflight", "hosted_pytest", "extra_hosted_job"])
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
    else:
        tests["jobs"]["paid-overflow"] = {"runs-on": "ubuntu-latest"}
    assert cost_policy_findings(tests, preflight), mutation
