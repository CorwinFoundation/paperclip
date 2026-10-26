"""Bound matrix concurrency to the two existing isolated heavy-runner slots."""
from copy import deepcopy
from pathlib import Path
import re

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
LOCAL_PREDICATE = "contains(needs.changes.outputs.runner, 'backbond-ci')"
LIMIT_PAIR = re.compile(
    r"\$\{\{\s*" + re.escape(LOCAL_PREDICATE)
    + r"\s*&&\s*(\d+)\s*\|\|\s*(\d+)\s*\}\}"
)
WORKERS = re.compile(
    r"\$\{\{\s*" + re.escape(LOCAL_PREDICATE)
    + r"\s*&&\s*'(\d+)'\s*\|\|\s*needs\.changes\.outputs\.pytest_workers"
    + r"\s*\|\|\s*'auto'\s*\}\}"
)


def workflow():
    return yaml.load((ROOT / ".github/workflows/tests.yml").read_text(), Loader=yaml.BaseLoader)


def limits(expression):
    match = LIMIT_PAIR.fullmatch(" ".join(str(expression).split()))
    return tuple(map(int, match.groups())) if match else None


def capacity_findings(value):
    errors = []
    job = value["jobs"]["pytest"]
    if limits(job["strategy"].get("max-parallel")) != (2, 6):
        errors.append("pytest must allow two local slots and preserve six hosted slots")
    workers = WORKERS.fullmatch(" ".join(str(job["env"].get("PYTEST_WORKERS")).split()))
    if not workers or int(workers[1]) != 2:
        errors.append("a local shard must retain its existing two pytest processes")
    command = next(step["run"] for step in job["steps"] if step.get("name") == "Run tests")
    if '-n "$PYTEST_WORKERS" --dist loadfile' not in " ".join(command.split()):
        errors.append("pytest must use the bounded worker count and file-local scheduling")
    if str(job["strategy"].get("fail-fast")).lower() != "false":
        errors.append("every shard must report to the required aggregate")
    if limits(value["jobs"]["eval-framework-venvs"]["strategy"]["max-parallel"]) != (1, 4):
        errors.append("evaluator concurrency is outside the pytest-only change")
    return errors


def test_two_vm_pytest_capacity_preserves_other_execution_bounds():
    assert capacity_findings(workflow()) == []


@pytest.mark.parametrize("local,hosted", [(1, 6), (3, 6), (0, 6), (2, 7), (2, 0)])
def test_capacity_guard_rejects_serial_regressions_and_extra_execution_slots(local, hosted):
    value = deepcopy(workflow())
    value["jobs"]["pytest"]["strategy"]["max-parallel"] = (
        "${{ " + LOCAL_PREDICATE + f" && {local} || {hosted} " + "}}"
    )
    assert capacity_findings(value)


@pytest.mark.parametrize("mutation", ["dynamic_budget", "extra_processes", "unbounded_command", "fail_fast", "eval_expansion"])
def test_capacity_guard_rejects_unreviewed_resource_and_coverage_changes(mutation):
    value = deepcopy(workflow())
    job = value["jobs"]["pytest"]
    if mutation == "dynamic_budget":
        job["strategy"]["max-parallel"] = "${{ vars.UNREVIEWED_PARALLELISM }}"
    elif mutation == "extra_processes":
        job["env"]["PYTEST_WORKERS"] = job["env"]["PYTEST_WORKERS"].replace("&& '2'", "&& '4'")
    elif mutation == "unbounded_command":
        step = next(step for step in job["steps"] if step.get("name") == "Run tests")
        step["run"] = step["run"].replace('-n "$PYTEST_WORKERS"', '-n auto')
    elif mutation == "fail_fast":
        job["strategy"]["fail-fast"] = "true"
    else:
        value["jobs"]["eval-framework-venvs"]["strategy"]["max-parallel"] = (
            "${{ " + LOCAL_PREDICATE + " && 2 || 4 }}"
        )
    assert capacity_findings(value), mutation
