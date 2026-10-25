#!/usr/bin/env python3
"""Read-only cgroup evidence for the bounded control worker; never print env."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def findings(data):
    errors = []
    for key, maximum in (("memory.max", 1024**3), ("pids.max", 128)):
        value = data[key]
        if type(value) is not int or not 0 < value <= maximum:
            errors.append(key)
    if data["memory.swap.max"] != 0:
        errors.append("memory.swap.max")
    quota, period = data["cpu.max"]
    if type(quota) is not int or type(period) is not int or not 0 < quota <= period / 4:
        errors.append("cpu.max")
    if data["uid"] == 0 or data["NoNewPrivs"] != 1 or data["oom_score_adj"] != 1000:
        errors.append("process isolation")
    if data["memory.events"].get("oom_kill", -1) != 0:
        errors.append("recorded OOM kill")
    return errors


def collect():
    root = Path("/sys/fs/cgroup")
    def number(value):
        return int(value) if value.isdigit() else value
    def counters(name):
        return {line.split()[0]: int(line.split()[1]) for line in (root / name).read_text().splitlines()}
    data = {name: number((root / name).read_text().strip()) for name in
            ("memory.max", "memory.swap.max", "memory.current", "memory.peak", "pids.max")}
    data["cpu.max"] = [number(value) for value in (root / "cpu.max").read_text().split()]
    data["memory.events"] = counters("memory.events")
    stats = counters("memory.stat")
    data["memory.stat"] = {name: stats[name] for name in ("anon", "file")}
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines())
    data.update(uid=os.getuid(), NoNewPrivs=int(status["NoNewPrivs"]),
                oom_score_adj=int(Path("/proc/self/oom_score_adj").read_text()))
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    data = collect()
    data["findings"] = findings(data)
    print(json.dumps(data, sort_keys=True))
    if args.check and data["findings"]:
        raise SystemExit("control-worker isolation check failed")


if __name__ == "__main__":
    main()
