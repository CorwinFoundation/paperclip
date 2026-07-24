import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from evals.aiuc_path_b import lane_a_depth as lane


EXPECTED_ROOT = Path("/var/lib/are-scanner/evidence/wave2/single-run-leases/v0.1")


def test_official_lease_root_is_immutable_and_ignores_environment(monkeypatch):
    monkeypatch.setenv("BACKBOND_WAVE2_LEASE_ROOT", "/tmp/workspace-local-bypass")
    monkeypatch.setenv("BACKBOND_WAVE2_PYTEST_LEASE_ROOT", "/tmp/pytest-bypass")
    assert lane.OFFICIAL_RUN_LEASE_ROOT == EXPECTED_ROOT


def test_alternate_store_cannot_enter_official_entrypoint(tmp_path):
    store = lane.AtomicSingleRunLeaseStore(tmp_path / "workspace-local-leases")
    lease = store.acquire({"authority": "test"}, owner="BEAAA-17479")

    with pytest.raises(lane.TargetExecutionError) as excinfo:
        with lane._official_entrypoint_context(store, lease):
            pytest.fail("alternate root entered official execution context")

    assert excinfo.value.code == "official_lease_root_mismatch"


def test_direct_family_runner_rejects_before_target_call(monkeypatch):
    calls = 0

    def counted_run(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("target transport must remain unreachable")

    monkeypatch.setattr(subprocess, "run", counted_run)
    auth = lane.reference_authorization(commit_sha="f" * 40, parent_sha="e" * 40)

    with pytest.raises(lane.TargetExecutionError) as excinfo:
        lane._execute_target_command(auth, {"prompt": "must not execute"})

    assert excinfo.value.code == "official_entrypoint_required"
    assert calls == 0


def test_direct_official_wrapper_rejects_before_runner():
    env = dict(os.environ)
    env.pop("BACKBOND_WAVE2_OFFICIAL_LEASE_ROOT", None)
    env.pop("BACKBOND_WAVE2_AUTHORITY_KEY", None)
    proc = subprocess.run(
        [sys.executable, str(lane.OFFICIAL_TARGET_WRAPPER)],
        input=json.dumps({"prompt": "must not execute"}),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert proc.returncode == 78
    assert "official Wave 2 entrypoint lease required" in proc.stderr


@pytest.mark.parametrize(
    ("shape", "expected_code"),
    (
        ("missing", "authorization_lease_store_missing"),
        ("missing_consumed", "authorization_lease_store_malformed"),
        ("missing_active", "authorization_lease_store_malformed"),
    ),
)
def test_official_store_shape_fails_closed(tmp_path, shape, expected_code):
    root = tmp_path / "official"
    if shape != "missing":
        root.mkdir()
    if shape not in {"missing_active", "missing"}:
        (root / "active").mkdir()
    if shape not in {"missing_consumed", "missing"}:
        (root / "consumed").mkdir()

    with pytest.raises(lane.TargetExecutionError) as excinfo:
        lane.AtomicSingleRunLeaseStore(root, require_preprovisioned=True)

    assert excinfo.value.code == expected_code


def test_any_active_residue_blocks_before_transport(tmp_path):
    store = lane.AtomicSingleRunLeaseStore(tmp_path / "official")
    (store.active_dir / "quarantined-alternate-residue").mkdir()

    with pytest.raises(lane.TargetExecutionError) as excinfo:
        store.acquire({"authority": "fresh"}, owner="BEAAA-17479")

    assert excinfo.value.code.startswith("authorization_lease_active_residue:")
