"""Wrap `bin/are-scan` via subprocess.

The SDK does not duplicate rubric or scoring math. It builds the input
dir, calls bin/are-scan, and parses the contract-shaped JSON output. This
is the supervision-contract-required "wrap, don't fork" posture.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ScanError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScanResult:
    contract_output: dict
    stdout: str
    stderr: str


def run_master_scan(
    *,
    scanner_bin: str,
    agent_id: str,
    input_dir: Path,
    seed: int,
    corpus_version: str,
    scanner_version: str,
) -> ScanResult:
    cmd = [
        scanner_bin,
        "--agent", agent_id,
        "--input-dir", str(input_dir),
        "--seed", str(seed),
        "--corpus", corpus_version,
        "--version", scanner_version,
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ScanError(
            f"bin/are-scan exited {proc.returncode}\n"
            f"cmd: {' '.join(cmd)}\n"
            f"stderr:\n{proc.stderr}\n"
            f"stdout:\n{proc.stdout}\n"
        )
    try:
        contract_output = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ScanError(
            f"bin/are-scan stdout not JSON: {e}\nstdout:\n{proc.stdout}"
        )
    return ScanResult(contract_output=contract_output,
                      stdout=proc.stdout, stderr=proc.stderr)
