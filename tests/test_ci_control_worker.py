"""Small controls must never become a second heavy or credentialed worker."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("control_worker_probe", ROOT / "scripts/ci/control_worker_probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)

CONTROL_JOBS = {
    ("tests.yml", "changes"), ("tests.yml", "pytest-aggregate"),
    ("pr-unit-tests.yml", "changes"),
    ("version-control-preflight.yml", "release-identity"),
    ("version-control-preflight.yml", "policy"),
    ("ci-control-smoke.yml", "control-smoke"),
}


def profile():
    return {"memory.max": 1024**3, "memory.swap.max": 0, "pids.max": 128,
            "cpu.max": [25000, 100000], "uid": 1001, "NoNewPrivs": 1,
            "oom_score_adj": 1000, "memory.events": {"oom_kill": 0}}


def test_bounded_control_profile_is_accepted():
    assert probe.findings(profile()) == []


@pytest.mark.parametrize("key,value", [
    ("memory.max", "max"), ("memory.max", 1024**3 + 1),
    ("memory.swap.max", 1), ("pids.max", 129), ("pids.max", "max"),
    ("cpu.max", [25001, 100000]), ("cpu.max", ["max", 100000]),
    ("uid", 0), ("NoNewPrivs", 0), ("oom_score_adj", 0),
    ("memory.events", {"oom_kill": 1}), ("memory.events", {}),
])
def test_unbounded_or_failed_control_is_rejected(key, value):
    data = profile()
    data[key] = value
    assert probe.findings(data)


def test_only_reviewed_small_jobs_use_control_capacity():
    actual = set()
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        for name, job in workflow.get("jobs", {}).items():
            if "backbond-ci-control" in str(job.get("runs-on")):
                actual.add((path.name, name))
                permissions = job.get("permissions", workflow.get("permissions"))
                assert permissions and all(v == "read" for v in permissions.values())
                assert "secrets." not in yaml.dump(job)
                assert int(job["timeout-minutes"]) <= 10
    assert actual == CONTROL_JOBS


def test_heavy_selector_output_and_all_cost_guards_still_fail_closed():
    spec = importlib.util.spec_from_file_location("cost_guard_control", ROOT / "tests/test_founder_ci_cost_policy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tests, preflight = module.workflows()
    assert module.cost_policy_findings(tests, preflight) == []
    mutated = deepcopy(tests)
    mutated["jobs"]["changes"]["outputs"]["runner"] = module.QUEUED_SELECTOR.replace(
        '"backbond-ci"', '"backbond-ci-control"')
    assert module.cost_policy_findings(mutated, preflight)
    for job in ("pytest", "min-python-cli-render"):
        mutated = deepcopy(tests)
        mutated["jobs"][job]["runs-on"] = module.CONTROL_DIRECT
        assert module.cost_policy_findings(mutated, preflight)


def test_control_smoke_has_no_hosted_fallback_and_uses_existing_opt_in():
    path = ROOT / ".github/workflows/ci-control-smoke.yml"
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    job = workflow["jobs"]["control-smoke"]
    assert job["runs-on"] == ["self-hosted", "linux", "x64", "backbond-ci-control"]
    assert "BACKBOND_LOCAL_CI == 'true'" in job["if"]
    assert "github.actor == 'CorwinFoundation' || github.actor == 'backbond-qa'" in job["if"]
    assert "github.event.pull_request.head.repo.full_name == github.repository" in job["if"]
    assert "pull_request_target" not in workflow["on"]


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
@pytest.mark.parametrize("mismatch", [None, "agentName", "gitHubUrl", "workFolder"])
def test_actual_startup_reader_accepts_dotnet_bom_and_preserves_identity_guards(tmp_path, encoding, mismatch):
    # Execute the actual embedded reader, including its repository/name checks.
    source = (ROOT / "scripts/ci/start-control-runner.sh").read_text()
    line = next(line.strip() for line in source.splitlines()
                if line.strip().startswith("'import json,pathlib;"))
    reader, = shlex.split(line)
    data = dict(agentName="backbond-ci-control-srv1799443",
                gitHubUrl="https://github.com/CorwinFoundation/are-scanner", workFolder="_work")
    if mismatch:
        data[mismatch] = "unexpected"
    (tmp_path / ".runner").write_text(json.dumps(data), encoding=encoding)
    result = subprocess.run([sys.executable, "-c", reader], cwd=tmp_path, capture_output=True, text=True)
    assert (result.returncode == 0) is (mismatch is None), result.stderr
