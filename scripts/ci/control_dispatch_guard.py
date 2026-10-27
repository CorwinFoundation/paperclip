"""Bounded recovery of an idle local control listener; never rerun a job.

Called under the existing build broker's exclusive lock. Credentials stay in
that broker, and its build reservation always takes precedence.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time

from build_pool_policy import ACTORS, REPOSITORY

LABEL = "backbond-ci-control"
NAME = "backbond-ci-control-srv1799443"
SERVICE = "backbond-ci-control-runner.service"
STALL_SECONDS = 300
COOLDOWN_SECONDS = 1800
QUIET_SECONDS = 65
POLL_SECONDS = 60


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.timestamp() if result.tzinfo is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def stalled_aggregate(run, job, now):
    """Only the trusted, ready final gate in the existing local control pool."""
    created = timestamp(job.get("created_at"))
    return (
        run.get("path", "").split("@", 1)[0] == ".github/workflows/tests.yml"
        and run.get("repository", {}).get("full_name") == REPOSITORY
        and run.get("head_repository", {}).get("full_name") == REPOSITORY
        and run.get("actor", {}).get("login") in ACTORS
        and run.get("triggering_actor", {}).get("login") in ACTORS
        and run.get("event") in {"pull_request", "push", "workflow_dispatch", "schedule"}
        and bool(re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha", "")))
        and type(run.get("id")) is int and run["id"] > 0
        and type(run.get("run_attempt")) is int and run["run_attempt"] > 0
        and type(job.get("run_id")) is int and type(job.get("run_attempt")) is int
        and job.get("run_id") == run.get("id")
        and job.get("run_attempt") == run["run_attempt"]
        and job.get("name") == "python pytest" and job.get("status") == "queued"
        and type(job.get("id")) is int and job["id"] > 0
        and not job.get("runner_id")
        and {str(x).lower() for x in job.get("labels", [])}
        == {"self-hosted", "linux", "x64", LABEL}
        and created is not None and STALL_SECONDS <= now - created < 86400
    )


def atomic_json(path, value):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=path.name + ".", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary.replace(path)
        if os.name == "posix":
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    if not path.exists():
        return {}
    info = path.lstat()
    if path.is_symlink() or (os.name == "posix" and
            (info.st_uid != os.geteuid() or info.st_mode & 0o077)):
        raise RuntimeError("untrusted control dispatch state; no recovery permitted")
    return json.loads(path.read_text(encoding="utf-8"))


class LocalControl:
    """Fixed local account/service; no guest-provided commands or credentials."""
    def __init__(self):
        import pwd
        account = pwd.getpwuid(1006)
        if socket.gethostname() not in {"backbond-fired-probe-operator-prod", "srv1799443", "srv1799443.hstgr.cloud"} or account.pw_dir != "/var/lib/backbond-ci-control":
            raise RuntimeError("control recovery is only configured for the original CI host")
        self.account = account.pw_name
        self.home = Path(account.pw_dir).resolve()
        self.group = Path("/sys/fs/cgroup/user.slice/user-1006.slice")

    def processes(self):
        if not self.group.is_dir():
            raise RuntimeError("control cgroup is absent")
        result = []
        for file in self.group.rglob("cgroup.procs"):
            try:
                pids = file.read_text().split()
            except FileNotFoundError:  # An empty transient cgroup disappeared.
                continue
            for pid in pids:
                try:
                    result.append(Path("/proc", pid, "comm").read_text().strip())
                except FileNotFoundError:
                    pass
        return result

    def workers(self):
        return "Runner.Worker" in self.processes()

    def service(self, verb):
        if verb not in {"is-active", "restart"}:
            raise ValueError("unsupported control operation")
        return subprocess.run(["runuser", "-u", self.account, "--", "env",
            "XDG_RUNTIME_DIR=/run/user/1006", "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1006/bus",
            "systemctl", "--user", verb, SERVICE], capture_output=True,
            text=True, timeout=90, check=False)

    def healthy(self, since=None):
        if self.service("is-active").returncode or "Runner.Listener" not in self.processes():
            return False
        if since is None:
            return True
        for path in self.home.glob(".local/share/containers/storage/volumes/*/_data/runner/_diag/Runner_*.log"):
            if path.is_symlink() or not path.resolve().is_relative_to(self.home) or path.stat().st_mtime < since:
                continue
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 65536))
                text = stream.read(65536).decode("utf-8", errors="replace")
            for value in re.findall(r"^\[([^\]]+) INFO (?:BrokerMessageListener|MessageListener)\] Session created\.", text, re.MULTILINE):
                instant = timestamp(value.replace(" ", "T", 1))
                if instant is not None and instant >= since - 1:
                    return True
        return False


class ControlDispatchGuard:
    def __init__(self, github, state_dir, local=None, now=time.time, sleep=time.sleep):
        self.github, self.root, self.local = github, Path(state_dir), local
        self.now, self.sleep = now, sleep
        self.state = self.root / "control-dispatch-state.json"
        self.journal = self.root / "control-dispatch-recovery.json"

    def event(self, name, **data):
        print(json.dumps({"event": name, **data}, separators=(",", ":")), flush=True)

    def identity(self):
        identity = self.github.config.get("control_runner_id")
        if type(identity) is not int or identity <= 0 or identity in {21, 22, 83, 98}:
            raise RuntimeError("invalid local control recovery binding")
        return identity

    def runner(self):
        result = self.github.request("GET", f"/repos/{REPOSITORY}/actions/runners/{self.identity()}")
        all_labels = {x["name"].lower() for x in result["labels"]}
        labels = all_labels - {"self-hosted", "linux", "x64"}
        if result.get("id") != self.identity() or result.get("name") != NAME or not {"self-hosted", "linux", "x64"} <= all_labels or labels not in (set(), {LABEL}):
            raise RuntimeError("local control identity or role changed; recovery closed")
        return result, labels

    def idle(self, allow_offline=False):
        runner, labels = self.runner()
        return not labels and (allow_offline or runner.get("status") == "online") and runner.get("busy") is False and not self.local.workers()

    def restore(self):
        _, labels = self.runner()
        if LABEL not in labels:
            self.github.request("POST", f"/repos/{REPOSITORY}/actions/runners/{self.identity()}/labels", {"labels": [LABEL]})
        self.journal.unlink()

    def refresh(self, transaction, resume=False):
        # Label withdrawal prevents new assignments; allow an in-flight lease
        # its documented 60-second pickup/requeue interval before any restart.
        for _ in range(QUIET_SECONDS // 5):
            self.sleep(5)
            if not self.idle(allow_offline=resume):
                if transaction["phase"] == "restart-issued" and not self.local.healthy():
                    raise RuntimeError("unhealthy control recovery remains quarantined")
                self.restore()
                self.event("control_dispatch_refresh_aborted_busy", job=transaction["job"])
                return False
        if not self.idle(allow_offline=resume):
            self.restore()
            return False
        target = self.github.request("GET", f"/repos/{REPOSITORY}/actions/jobs/{transaction['job']}")
        if target.get("status") != "queued" or target.get("runner_id"):
            if transaction["phase"] == "restart-issued" and not self.local.healthy(transaction["restarted_at"]):
                raise RuntimeError("completed target but control recovery still needs inspection")
            self.restore()
            self.event("control_dispatch_resolved_without_restart", job=transaction["job"])
            return False
        if not self.idle(allow_offline=resume):
            self.restore()
            return False
        transaction.update(phase="restart-issued", restarted_at=self.now())
        atomic_json(self.journal, transaction)
        if self.local.service("restart").returncode:
            raise RuntimeError("idle control restart failed; route remains withheld")
        for _ in range(18):
            if self.idle() and self.local.healthy(transaction["restarted_at"]):
                self.restore()
                self.event("control_dispatch_listener_refreshed", job=transaction["job"], runner=self.identity())
                return True
            self.sleep(5)
        raise RuntimeError("fresh control session not verified; route remains withheld")

    def recover(self):
        transaction = read_json(self.journal)
        if not transaction:
            return
        if transaction.get("runner") != self.identity() or transaction.get("name") != NAME or type(transaction.get("job")) is not int or transaction["job"] <= 0 or transaction.get("phase") not in {"withdrawn", "restart-issued"}:
            raise RuntimeError("invalid control recovery journal")
        self.local = self.local or LocalControl()
        self.runner()
        if transaction["phase"] == "withdrawn" and self.local.healthy():
            self.restore()  # Controller exited before requesting a restart.
            return
        if transaction["phase"] == "restart-issued" and self.local.healthy(transaction["restarted_at"]):
            self.restore()
            return
        if transaction.get("resume_attempted") or self.local.workers():
            raise RuntimeError("control recovery requires inspection; no active work will be stopped")
        transaction["resume_attempted"] = True
        atomic_json(self.journal, transaction)
        self.refresh(transaction, resume=True)

    def tick(self):
        if self.github.config.get("control_dispatch_guard_enabled") is not True:
            return
        if (self.root / "admission.json").exists() or self.journal.exists():
            return
        state = read_json(self.state)
        now = self.now()
        if now - state.get("last_poll", 0) < POLL_SECONDS:
            return
        state["last_poll"] = now
        # Eligible jobs are younger than 24 hours. Retain every attempted ID
        # for that whole window, including when different runs alternate.
        attempts = {key: value for key, value in state.get("attempts", {}).items()
                    if now - value["at"] < 86400}
        state["attempts"] = attempts
        atomic_json(self.state, state)
        self.local = self.local or LocalControl()
        runner, labels = self.runner()
        if labels != {LABEL} or runner.get("status") != "online" or runner.get("busy") is not False or self.local.workers() or not self.local.healthy():
            return
        scanned = 0
        for status in ("queued", "in_progress"):
            runs = self.github.request("GET", f"/repos/{REPOSITORY}/actions/runs?status={status}&per_page=100")["workflow_runs"]
            for run in sorted(runs, key=lambda x: x.get("id", 0)):
                if run.get("path", "").split("@", 1)[0] != ".github/workflows/tests.yml":
                    continue
                if type(run.get("id")) is not int or run["id"] <= 0 or type(run.get("run_attempt")) is not int or run["run_attempt"] <= 0:
                    continue
                if scanned >= 8 or self.now() - now > 30:
                    self.event("control_dispatch_observation_bounded")
                    return
                scanned += 1
                jobs = self.github.request("GET", f"/repos/{REPOSITORY}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100")["jobs"]
                for job in jobs:
                    if not stalled_aggregate(run, job, now):
                        continue
                    previous = attempts.get(str(job["id"]))
                    if previous:
                        if now - previous["at"] >= 180 and not previous.get("unresolved_reported"):
                            previous["unresolved_reported"] = True
                            atomic_json(self.state, state)
                            self.event("control_dispatch_stall_unresolved", job=job["id"], queued_seconds=int(now - timestamp(job["created_at"])))
                        return  # One attempt per job; never loop over its identity.
                    if now - state.get("last_attempt", 0) < COOLDOWN_SECONDS:
                        return
                    state.update(last_attempt=now, last_job=job["id"])
                    attempts[str(job["id"])] = {"at": now, "unresolved_reported": False}
                    atomic_json(self.state, state)
                    transaction = {"runner": self.identity(), "name": NAME, "job": job["id"], "phase": "withdrawn"}
                    atomic_json(self.journal, transaction)
                    self.event("control_dispatch_stall", job=job["id"], queued_seconds=int(now - timestamp(job["created_at"])))
                    self.github.request("DELETE", f"/repos/{REPOSITORY}/actions/runners/{self.identity()}/labels/{LABEL}")
                    self.refresh(transaction)
                    return
