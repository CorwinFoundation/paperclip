"""Failure/lease safety for queue-only idle control recovery, without credentials."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def scenario(monkeypatch, tmp_path):
    source = Path(__file__).resolve().parents[1] / "scripts/ci"
    spec = importlib.util.spec_from_file_location("build_pool_policy", source / "build_pool_policy.py")
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    monkeypatch.setitem(sys.modules, "build_pool_policy", policy)
    spec = importlib.util.spec_from_file_location("dispatch_guard_test", source / "control_dispatch_guard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    clock = SimpleNamespace(value=10000.0, on_sleep=lambda: None)
    def sleep(seconds):
        clock.value += seconds
        clock.on_sleep()
    run = dict(id=10, run_attempt=1, path=".github/workflows/tests.yml", head_sha="a"*40,
               repository={"full_name": policy.REPOSITORY}, head_repository={"full_name": policy.REPOSITORY},
               actor={"login": "CorwinFoundation"}, triggering_actor={"login": "CorwinFoundation"}, event="pull_request")
    job = dict(id=20, run_id=10, run_attempt=1, name="python pytest", status="queued", runner_id=0,
               labels=["self-hosted", "linux", "x64", module.LABEL],
               created_at=datetime.fromtimestamp(9400, timezone.utc).isoformat())
    class GitHub:
        config = {"control_runner_id":29, "control_dispatch_guard_enabled":True}
        labels, busy, online, name = {module.LABEL}, False, True, module.NAME
        calls = []
        def request(self, method, path, data=None):
            self.calls.append((method,path,data))
            if "/labels" in path:
                assert guard.journal.exists(), "write-ahead journal precedes every label mutation"
                if method == "DELETE": self.labels=set()
                elif method == "POST":
                    assert data=={"labels":[module.LABEL]}
                    self.labels={module.LABEL}
                else: raise AssertionError((method,path))
                return
            assert method=="GET", "No job rerun, registration or credential operations"
            if path.endswith("/actions/runners/29"):
                return dict(id=29,name=self.name,busy=self.busy,status="online" if self.online else "offline",labels=[{"name":x} for x in {"self-hosted","linux","x64"}|self.labels])
            if "/actions/runs?" in path:
                return {"workflow_runs": [run] if "status=queued" in path else []}
            if path.endswith("/attempts/1/jobs?per_page=100"):return {"jobs":[job]}
            if path.endswith("/actions/jobs/"+str(job["id"])):return job
            raise AssertionError(path)
    github=GitHub()
    class Local:
        busy=False
        fresh=True
        active=True
        restarts=0
        def workers(self):return self.busy
        def healthy(self,since=None):return self.active and (since is None or self.fresh)
        def service(self,verb):
            assert verb=="restart" and not self.busy and not github.labels
            assert clock.value>=10000+module.QUIET_SECONDS
            assert json.loads(guard.journal.read_text())["phase"]=="restart-issued"
            self.restarts+=1
            return SimpleNamespace(returncode=0)
    local=Local()
    guard=module.ControlDispatchGuard(github,tmp_path,local,lambda:clock.value,sleep)
    return SimpleNamespace(module=module,guard=guard,github=github,local=local,clock=clock,run=run,job=job,root=tmp_path)


def test_idle_refresh_is_journaled_quiesced_and_restores_only_existing_role(scenario):
    s=scenario
    s.guard.tick()
    assert s.local.restarts==1 and s.github.labels=={s.module.LABEL}
    assert not s.guard.journal.exists()
    assert json.loads(s.guard.state.read_text())["last_job"]==20


@pytest.mark.parametrize("mutation",["disabled","reserved","busy_api","busy_local","offline","label_withheld","foreign_role","wrong_name","unhealthy"])
def test_unavailable_or_unverified_capacity_is_never_restarted(scenario,mutation):
    s=scenario
    if mutation=="disabled":s.github.config={**s.github.config,"control_dispatch_guard_enabled":False}
    elif mutation=="reserved":(s.root/"admission.json").write_text("{}")
    elif mutation=="busy_api":s.github.busy=True
    elif mutation=="busy_local":s.local.busy=True
    elif mutation=="offline":s.github.online=False
    elif mutation=="label_withheld":s.github.labels=set()
    elif mutation=="foreign_role":s.github.labels={"backbond-ci"}
    elif mutation=="wrong_name":s.github.name="other-runner"
    else:s.local.active=False
    if mutation in {"wrong_name","foreign_role"}:
        with pytest.raises(RuntimeError):s.guard.tick()
    else:s.guard.tick()
    assert s.local.restarts==0
    assert not any(c[0] in {"POST","DELETE"} for c in s.github.calls)


@pytest.mark.parametrize("mutation",["actor","rerun_actor","fork","workflow","attempt","target_id","label","assigned","name","fresh","future","timestamp"])
def test_only_old_trusted_unassigned_aggregate_can_trigger_recovery(scenario,mutation):
    s=scenario
    if mutation=="actor":s.run["actor"]={"login":"outsider"}
    elif mutation=="rerun_actor":s.run["triggering_actor"]={"login":"outsider"}
    elif mutation=="fork":s.run["head_repository"]={"full_name":"other/fork"}
    elif mutation=="workflow":s.run["path"]=".github/workflows/other.yml"
    elif mutation=="attempt":s.job["run_attempt"]=2
    elif mutation=="target_id":s.job["run_id"]=99
    elif mutation=="label":s.job["labels"].append("backbond-publish")
    elif mutation=="assigned":s.job["runner_id"]=98
    elif mutation=="name":s.job["name"]="python pytest (1/6)"
    elif mutation=="fresh":s.job["created_at"]=datetime.fromtimestamp(9900,timezone.utc).isoformat()
    elif mutation=="future":s.job["created_at"]=datetime.fromtimestamp(11000,timezone.utc).isoformat()
    else:s.job["created_at"]="not-a-time"
    assert not s.module.stalled_aggregate(s.run,s.job,s.clock.value)
    s.guard.tick()
    assert s.local.restarts==0


@pytest.mark.parametrize("activity",["local","api","resolved","route_restored"])
def test_activity_or_resolution_during_quiescence_aborts_without_restart(scenario,activity):
    s=scenario
    def change():
        if activity=="local":s.local.busy=True
        elif activity=="api":s.github.busy=True
        elif activity=="resolved":s.job["status"]="completed"
        else:s.github.labels={s.module.LABEL}
    s.clock.on_sleep=change
    s.guard.tick()
    assert s.local.restarts==0 and s.github.labels=={s.module.LABEL}
    assert not s.guard.journal.exists()


def test_no_automatic_loop_on_same_job_and_global_cooldown(scenario,capsys):
    s=scenario
    s.guard.tick()
    s.clock.value+=3600
    s.guard.tick()
    assert s.local.restarts==1 and "stall_unresolved" in capsys.readouterr().out
    s.guard.tick()
    assert s.local.restarts==1


def test_distinct_jobs_still_respect_global_cooldown(scenario):
    s=scenario
    s.guard.tick()
    s.job["id"]=21
    s.clock.value+=300
    s.guard.tick()
    assert s.local.restarts==1
    s.clock.value+=1800
    s.guard.tick()
    assert s.local.restarts==2


def test_alternating_jobs_cannot_repeat_an_earlier_refresh(scenario):
    s=scenario
    s.guard.tick()
    s.clock.value+=1800
    s.job["id"]=21
    s.guard.tick()
    s.clock.value+=1800
    s.job["id"]=20
    s.guard.tick()
    assert s.local.restarts==2
    assert set(json.loads(s.guard.state.read_text())["attempts"])=={"20","21"}


def test_failed_fresh_session_leaves_role_withheld_and_durable_recovery(scenario):
    s=scenario
    s.local.fresh=False
    with pytest.raises(RuntimeError,match="fresh control session"):
        s.guard.tick()
    assert s.github.labels==set() and s.guard.journal.exists()
    s.local.fresh=True
    s.guard.recover()
    assert s.github.labels=={s.module.LABEL} and not s.guard.journal.exists()
    assert s.local.restarts==1


def test_interrupted_before_restart_can_restore_without_stopping_work(scenario):
    s=scenario
    s.module.atomic_json(s.guard.journal,dict(runner=29,name=s.module.NAME,job=20,phase="withdrawn"))
    s.github.labels=set()
    s.local.busy=True
    s.github.busy=True
    s.guard.recover()
    assert s.local.restarts==0 and s.github.labels=={s.module.LABEL}


def test_changed_registration_binding_never_receives_restored_role(scenario):
    s=scenario
    s.module.atomic_json(s.guard.journal,dict(runner=29,name=s.module.NAME,job=20,phase="withdrawn"))
    s.github.labels=set()
    s.github.name="replacement"
    with pytest.raises(RuntimeError,match="identity"):
        s.guard.recover()
    assert s.github.labels==set() and s.guard.journal.exists() and s.local.restarts==0


def test_restore_api_failure_is_recoverable_without_a_second_restart(scenario):
    s=scenario
    request=s.github.request
    fail={"value":True}
    def flaky(method,path,data=None):
        if method=="POST" and fail["value"]:raise RuntimeError("API unavailable")
        return request(method,path,data)
    s.github.request=flaky
    with pytest.raises(RuntimeError,match="API unavailable"):s.guard.tick()
    assert s.local.restarts==1 and s.github.labels==set() and s.guard.journal.exists()
    fail["value"]=False
    s.guard.recover()
    assert s.local.restarts==1 and s.github.labels=={s.module.LABEL}


def test_outside_identity_change_during_grace_never_stops_service(scenario):
    s=scenario
    s.clock.on_sleep=lambda:setattr(s.github,"name","replacement")
    with pytest.raises(RuntimeError,match="identity"):s.guard.tick()
    assert s.local.restarts==0 and s.github.labels==set() and s.guard.journal.exists()


def test_resume_failure_is_bounded_and_never_restores_unhealthy_route(scenario):
    s=scenario
    s.github.labels=set()
    s.local.fresh=False
    s.module.atomic_json(s.guard.journal,dict(runner=29,name=s.module.NAME,job=20,phase="restart-issued",restarted_at=9000))
    with pytest.raises(RuntimeError,match="fresh control session"):s.guard.recover()
    assert s.local.restarts==1 and s.guard.journal.exists() and not s.github.labels
    with pytest.raises(RuntimeError,match="requires inspection"):s.guard.recover()
    assert s.local.restarts==1 and not s.github.labels


def test_restart_command_is_fixed_to_existing_control_user_and_unit(scenario,monkeypatch):
    s=scenario
    calls=[]
    monkeypatch.setattr(s.module.subprocess,"run",lambda args,**kw:calls.append(args) or SimpleNamespace(returncode=0))
    local=object.__new__(s.module.LocalControl)
    local.account="backbond-ci-control"
    local.service("restart")
    assert calls==[["runuser","-u","backbond-ci-control","--","env","XDG_RUNTIME_DIR=/run/user/1006","DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1006/bus","systemctl","--user","restart","backbond-ci-control-runner.service"]]
    with pytest.raises(ValueError):local.service("stop")


def test_other_host_is_not_an_automatic_recovery_target(scenario,monkeypatch):
    s=scenario
    monkeypatch.setitem(sys.modules,"pwd",SimpleNamespace(getpwuid=lambda _:SimpleNamespace(pw_name="backbond-ci-control",pw_dir="/var/lib/backbond-ci-control")))
    monkeypatch.setattr(s.module.socket,"gethostname",lambda:"srv1701968")
    with pytest.raises(RuntimeError,match="original CI host"):s.module.LocalControl()
