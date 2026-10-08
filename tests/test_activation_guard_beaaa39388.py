from __future__ import annotations

import fcntl
import multiprocessing
import time

from fastapi.testclient import TestClient

from api.activation_guard import ACTIVATION_LOCK_ENV, ActivationInProgress, ActivationLease
from api.app import RUBRIC_PATH, create_app
from api.bundle import BundleStore
from rubric.scoring import Rubric


def _exclusive(path: str, entered, release) -> None:
    with open(path, "r+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        entered.set()
        release.wait(3)


def test_activation_waits_for_accepted_scan_and_blocks_late_admission(tmp_path):
    lock = tmp_path / "activation.lock"
    lock.touch()
    accepted = ActivationLease(path=str(lock))
    entered = multiprocessing.Event()
    release = multiprocessing.Event()
    proc = multiprocessing.Process(target=_exclusive, args=(str(lock), entered, release))
    proc.start()
    try:
        assert not entered.wait(0.15), "activation crossed an accepted scan"
        accepted.release()
        assert entered.wait(1), "activation did not begin after accepted work drained"

        started = time.monotonic()
        # A nonblocking probe represents admission: LOCK_SH must be unavailable
        # for the whole exclusive activation window.
        with open(lock, "rb") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                admitted = True
            except BlockingIOError:
                admitted = False
        assert not admitted
        assert time.monotonic() - started < 0.5
    finally:
        release.set()
        proc.join(2)
        accepted.release()
    assert proc.exitcode == 0


def test_admission_fails_fast_while_activation_holds_exclusive_lock(tmp_path):
    lock = tmp_path / "activation.lock"
    lock.touch()
    with lock.open("r+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        started = time.monotonic()
        try:
            ActivationLease(path=str(lock))
        except ActivationInProgress:
            pass
        else:
            raise AssertionError("admission crossed exclusive activation lock")
        assert time.monotonic() - started < 0.5


def test_async_route_stays_responsive_during_exclusive_activation(tmp_path, monkeypatch):
    lock = tmp_path / "activation.lock"
    lock.touch()
    monkeypatch.setenv(ACTIVATION_LOCK_ENV, str(lock))
    app = create_app(rubric=Rubric.load(RUBRIC_PATH), bundles=BundleStore(), scan_runtime=None)
    client = TestClient(app, raise_server_exceptions=False)

    with lock.open("r+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        started = time.monotonic()
        response = client.post("/scanner/v1/scans", json={})
        assert time.monotonic() - started < 0.5

    assert response.status_code == 503
    assert response.json() == {
        "error": "admission_unavailable",
        "reason": "activation_in_progress",
    }
    assert response.headers["Retry-After"] == "1"
    assert client.get("/openapi.json").status_code == 200
