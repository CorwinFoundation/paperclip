"""BEAAA-42967: ``scanner-deploy qa-restart-fixture restart-test <scan-id>``.

The subcommand restarts the live container without the activation lock so the
startup recovery path runs while a fixture scan is in flight. It must refuse,
and restart nothing, unless the only unfinished work in both durable stores is
that one credential-free fixture scan.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from api.accepted_scan_store import _SCHEMA_SQL as ACCEPTED_SCHEMA
from api.full_scan_job_persistence import _SCHEMA_SQL as JOBS_SCHEMA

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "deploy" / "scanner-deploy.sh"
SCAN = "11111111-2222-4333-8444-555555555555"
OTHER = "99999999-2222-4333-8444-555555555555"
FIXTURE_URL = "https://scanner.backbond.ai/qa-restart-fixture/v1/chat/completions"


def _accepted(conn, scan_id, *, tenant="qa-38869-a", url=FIXTURE_URL, state="running", resumable=1):
    conn.execute(
        "INSERT INTO accepted_scans (scan_id, tenant_id, state, accepted_at, updated_at, "
        "resumable, request_json, context_json) VALUES (?, ?, ?, 0, 0, ?, ?, '{}')",
        (scan_id, tenant, state, resumable, json.dumps({"agent_endpoint": url})),
    )


@pytest.fixture()
def host(tmp_path):
    accepted = sqlite3.connect(tmp_path / "accepted.sqlite3", isolation_level=None)
    accepted.executescript(ACCEPTED_SCHEMA)
    jobs = sqlite3.connect(tmp_path / "jobs.sqlite3", isolation_level=None)
    jobs.executescript(JOBS_SCHEMA)
    calls = tmp_path / "calls.log"
    fake = tmp_path / "fake.sh"
    fake.write_text(
        f'#!/bin/sh\necho "$0 $*" >> {calls}\n[ "$1" = -sS ] && printf 200\n'
        f'[ "$1" = logs ] && echo "INFO accepted_scan_resumed scan_id={SCAN} key=sk-secret"\nexit 0\n'
    )
    fake.chmod(0o755)
    env = {
        "PATH": "/usr/bin:/bin",
        "SCANNER_ACCEPTED_SCAN_STORE": str(tmp_path / "accepted.sqlite3"),
        "SCANNER_FULL_SCAN_JOB_STORE": str(tmp_path / "jobs.sqlite3"),
        "SCANNER_SYSTEMCTL": str(fake),
        "SCANNER_CURL": str(fake),
        "SCANNER_DOCKER": str(fake),
    }

    def run(*args):
        return subprocess.run(["bash", str(HELPER), "qa-restart-fixture", "restart-test", *args],
                              capture_output=True, text=True, env=env, timeout=30)

    yield accepted, jobs, run, calls
    accepted.close()
    jobs.close()


def _restarted(calls: Path) -> bool:
    return calls.exists() and " restart backbond-scanner-live.service" in calls.read_text()


def test_restarts_when_only_the_fixture_scan_is_unfinished(host):
    accepted, jobs, run, calls = host
    _accepted(accepted, SCAN)
    _accepted(accepted, OTHER, tenant="real-tenant", url="https://customer.example/v1", state="terminal")
    jobs.execute("INSERT INTO full_scan_jobs VALUES ('j1', 'running', NULL, ?)", (json.dumps({"scan_id": SCAN}),))
    jobs.execute("INSERT INTO full_scan_jobs VALUES ('j2', 'complete', NULL, ?)", (json.dumps({"scan_id": OTHER}),))
    result = run(SCAN)
    assert result.returncode == 0, result.stderr
    assert _restarted(calls)
    assert f'"scan_id": "{SCAN}"' in result.stderr
    assert '"unfinished_full_scan_jobs": 1' in result.stderr
    assert "customer.example" not in result.stderr
    assert '"resume_count": 0' in result.stderr.split("post-restart", 1)[1]
    assert f"startup-recovery: accepted_scan_resumed scan_id={SCAN}" in result.stderr
    assert "sk-secret" not in result.stderr


@pytest.mark.parametrize("case", ["other_accepted", "credentialed", "foreign_tenant", "foreign_url",
                                  "terminal", "missing", "other_job", "unlabelled_job"])
def test_refuses_and_restarts_nothing(host, case):
    accepted, jobs, run, calls = host
    if case == "other_accepted":
        _accepted(accepted, SCAN)
        _accepted(accepted, OTHER, tenant="real-tenant", url="https://customer.example/v1", state="accepted")
    elif case == "credentialed":
        _accepted(accepted, SCAN, resumable=0)
    elif case == "foreign_tenant":
        _accepted(accepted, SCAN, tenant="real-tenant")
    elif case == "foreign_url":
        _accepted(accepted, SCAN, url="https://customer.example/qa-restart-fixture/v1/")
    elif case == "terminal":
        _accepted(accepted, SCAN, state="terminal")
    elif case in {"other_job", "unlabelled_job"}:
        _accepted(accepted, SCAN)
        body = {"scan_id": OTHER} if case == "other_job" else {}
        jobs.execute("INSERT INTO full_scan_jobs VALUES ('j9', 'queued', NULL, ?)", (json.dumps(body),))
    result = run(SCAN)
    assert result.returncode != 0
    assert "nothing was restarted" in result.stderr
    assert not _restarted(calls)
    assert "customer.example" not in result.stderr


def test_rejects_a_malformed_scan_id(host):
    _, _, run, calls = host
    result = run("../etc")
    assert result.returncode != 0
    assert "lowercase uuid" in result.stderr
    assert not _restarted(calls)
