#!/usr/bin/env python3
"""Root-only, queue-only broker. No web listener and no personal account token.

Install only after the documented isolation and recovery acceptance checks pass.
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import urllib.error
import urllib.request
import uuid

from build_pool_policy import REPOSITORY, admit
from build_pool_worker import STATE, Worker

# Both existing runners can consume 5 GiB on this 8 GiB VM. Drain both before
# admitting another heavy job; do not stop production services or running jobs.
EXISTING = (
    (REPOSITORY, 22, "backbond-ci-srv1799443"),
    ("CorwinFoundation/backbond-customer-operations", 21, "backbond-ci-operations"),
)
JOURNAL = STATE / "admission.json"


def custom_labels(runner):
    return {item["name"].lower() for item in runner["labels"]} - {"self-hosted", "linux", "x64"}


def encoded(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(value).rstrip(b"=")


class GitHub:
    def __init__(self, config):
        self.config, self.token, self.expires = config, None, 0

    def request(self, method, path, data=None, token=None):
        if token is None:
            token = self.installation_token()
        payload = None if data is None else json.dumps(data).encode()
        request = urllib.request.Request("https://api.github.com" + path, data=payload,
            method=method, headers={"Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "backbond-isolated-build-pool", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                content = response.read()
                return json.loads(content) if content else None
        except urllib.error.HTTPError as error:
            if method == "DELETE" and error.code == 404:
                return None
            # Never print HTTP response bodies: a token endpoint can return secrets.
            raise RuntimeError(f"GitHub {method} failed with HTTP {error.code}") from None

    def installation_token(self):
        if self.token and self.expires > time.time():
            return self.token
        now = int(time.time())
        unsigned = encoded({"alg": "RS256", "typ": "JWT"}) + b"." + encoded(
            {"iat": now - 60, "exp": now + 540, "iss": str(self.config["app_id"])})
        signature = subprocess.run(["openssl", "dgst", "-sha256", "-sign", self.config["key"]],
            input=unsigned, check=True, capture_output=True).stdout
        jwt = (unsigned + b"." + encoded(signature)).decode()
        result = self.request("POST", f"/app/installations/{self.config['installation_id']}/access_tokens",
                              {"repositories": ["are-scanner", "backbond-customer-operations"],
                               "permissions": {"administration": "write", "actions": "read", "contents": "read"}}, jwt)
        self.token, self.expires = result["token"], now + 3000
        return self.token

    def candidates(self):
        runs = []
        for status in ("queued", "in_progress"):
            runs.extend(self.request("GET", f"/repos/{REPOSITORY}/actions/runs?status={status}&per_page=100")["workflow_runs"])
        for run in sorted(runs, key=lambda x: x["id"]):
            # Avoid walking every shard of workflows which cannot use the pool.
            from build_pool_policy import JOBS
            if run.get("path", "").split("@", 1)[0] not in JOBS:
                continue
            jobs = self.request("GET", f"/repos/{REPOSITORY}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100")["jobs"]
            for job in jobs:
                expected = admit(run, job)
                if expected:
                    if expected["role"] == "publish":
                        comparison = self.request("GET", f"/repos/{REPOSITORY}/compare/{expected['head_sha']}...master")
                        if comparison.get("status") not in {"ahead", "identical"}:
                            continue
                    yield expected


def save(value):
    temporary = JOURNAL.with_suffix(".new")
    with temporary.open("w", encoding="utf-8") as target:
        os.fchmod(target.fileno(), 0o600)
        json.dump(value, target)
        target.flush()
        os.fsync(target.fileno())
    temporary.replace(JOURNAL)
    descriptor = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class Broker:
    def __init__(self, github):
        self.github = github

    def routes(self):
        # Registration IDs vary, but repository, name and custom label do not.
        # Missing configuration must never silently omit a live control worker.
        identity = self.github.config.get("control_runner_id")
        if type(identity) is not int or identity <= 0 or identity in {x[1] for x in EXISTING}:
            raise RuntimeError("control runner identity is not configured; admission closed")
        return tuple((*route, "backbond-ci") for route in EXISTING) + (
            (REPOSITORY, identity, "backbond-ci-control-srv1799443", "backbond-ci-control"),
        )

    def verify_egress(self):
        rules = subprocess.run(["nft", "--stateless", "list", "table", "inet", "backbond_build_pool"],
                               check=True, capture_output=True).stdout
        if hashlib.sha256(rules).hexdigest() != self.github.config["egress_sha256"]:
            raise RuntimeError("build egress rules changed; admission closed")

    def drain(self, state):
        # Record every intended removal durably before changing a label.
        routes = self.routes()
        for repository, identity, name, label in routes:
            base = f"/repos/{repository}/actions/runners/{identity}"
            runner = self.github.request("GET", base)
            if runner.get("name") != name or runner.get("status") != "online":
                raise RuntimeError("existing runner identity/health changed; admission closed")
            if label == "backbond-ci-control" and custom_labels(runner) != {label}:
                raise RuntimeError("control worker has unexpected routing labels; admission closed")
            if not any(item["name"] == label for item in runner["labels"]):
                raise RuntimeError("existing capacity is reserved elsewhere; admission closed")
            state["restore"].append({"base": base, "label": label})
            save(state)
            self.github.request("DELETE", base + "/labels/" + label)
        # Labels disappearing does not interrupt already assigned work. Require
        # stable idle observations after deletion before reserving memory.
        stable, deadline = 0, time.monotonic() + 3600
        while time.monotonic() < deadline:
            runners = [self.github.request("GET", f"/repos/{repo}/actions/runners/{identity}")
                       for repo, identity, _, _ in routes]
            if any(x.get("status") != "online" for x in runners):
                raise RuntimeError("existing runner disconnected while draining")
            if any(custom_labels(runner) for route, runner in zip(routes, runners)
                   if route[3] == "backbond-ci-control"):
                raise RuntimeError("control routing changed during admission")
            if any(any(item["name"] == route[3] for item in runner["labels"])
                   for route, runner in zip(routes, runners)):
                raise RuntimeError("another administrator restored routing during admission")
            # API idle alone can lag. Match any Worker on this dedicated CI VM,
            # including workers in subordinate UID namespaces.
            processes = subprocess.run(["ps", "-eo", "comm="], check=True, capture_output=True, text=True).stdout
            idle = not any(x.get("busy") for x in runners) and "Runner.Worker" not in {x.strip() for x in processes.splitlines()}
            stable = stable + 1 if idle else 0
            if stable >= 2:
                return
            time.sleep(15)
        raise RuntimeError("drain timed out; no new worker admitted")

    def recover(self):
        if not JOURNAL.exists():
            return
        state = json.loads(JOURNAL.read_text(encoding="utf-8"))
        if state.get("bootstrap"):
            raise RuntimeError("image preparation reservation requires inspection; admission closed")
        # Cleanup must succeed BEFORE other CI is allowed to consume memory.
        Worker(state["identity"], state["role"]).cleanup()
        if state.get("runner_id"):
            self.github.request("DELETE", f"/repos/{REPOSITORY}/actions/runners/{state['runner_id']}")
        for entry in state["restore"]:
            # Recover old journals too, including a crash during an upgrade.
            if isinstance(entry, str):
                base, label = entry, "backbond-ci"
            else:
                base, label = entry["base"], entry["label"]
            if label not in {"backbond-ci", "backbond-ci-control"}:
                raise RuntimeError("unknown restoration label; admission closed")
            self.github.request("POST", base + "/labels", {"labels": [label]})
        JOURNAL.unlink()

    def run_one(self, expected):
        state = {"identity": uuid.uuid4().hex, "role": expected["role"], "restore": []}
        save(state)
        try:
            self.verify_egress()
            self.drain(state)
            self.verify_egress()
            memory = Path("/proc/meminfo").read_text()
            available = next(int(line.split()[1]) for line in memory.splitlines()
                             if line.startswith("MemAvailable:"))
            if available < 6 * 1024 * 1024:
                raise RuntimeError("less than 6 GiB available; build remains queued")
            labels = ["self-hosted", "linux", "x64", f"backbond-{expected['role']}", expected["label"]]
            jit = self.github.request("POST", f"/repos/{REPOSITORY}/actions/runners/generate-jitconfig",
                {"name": f"bb-{expected['role']}-{state['identity']}", "runner_group_id": 1,
                 "labels": labels, "work_folder": "_work"})
            state["runner_id"] = jit["runner"]["id"]
            save(state)
            worker = Worker(state["identity"], expected["role"])
            worker.prepare(expected, jit["encoded_jit_config"])
            worker.wait(expected["minutes"])
        finally:
            self.recover()


def main():
    if os.geteuid() != 0:
        raise SystemExit("root-owned broker required")
    config_path = Path("/etc/backbond-build-pool/config.json")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    for path in (config_path, Path(config["key"])):
        info = path.stat()
        if info.st_uid != 0 or info.st_mode & 0o077:
            raise SystemExit("broker configuration and key must be root-owned mode 0600")
    STATE.mkdir(mode=0o711, exist_ok=True)
    # One controller, across service restarts and manual invocations.
    with (STATE / "broker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        broker = Broker(GitHub(config))
        broker.recover()
        while True:
            candidate = next(broker.github.candidates(), None)
            if candidate:
                print(f"Admitting {candidate['role']} job {candidate['run_id']}/{candidate['job']}", flush=True)
                broker.run_one(candidate)
                # Let ordinary queued CI resume between heavy builds.
                time.sleep(120)
            else:
                time.sleep(30)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    main()
