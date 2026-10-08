from __future__ import annotations

import asyncio
import threading
import time

import httpx

from api.app import create_app
from api.bundle import BundleStore
from api.scan_handlers import RunnerError, connectivity_check_handler
from rubric.scoring import Rubric


ENDPOINT = {
    "agent_endpoint": {
        "url": "https://learning.kaltura.com/",
        "transport": "kaltura_genie_browser",
        "auth": {"type": "none"},
    },
    "credential_secret": "",
}


def test_connectivity_api_runs_sync_handler_off_loop_and_loop_stays_responsive(monkeypatch):
    loop_thread: list[int] = []
    worker_thread: list[int] = []
    entered = threading.Event()
    release = threading.Event()

    def blocking_handler(_body):
        worker_thread.append(threading.get_ident())
        entered.set()
        assert release.wait(2)
        return 200, {"success": True, "error": None}

    monkeypatch.setattr("api.app.connectivity_check_handler", blocking_handler)
    rubric = Rubric.load("rubric/rubric.yaml")
    app = create_app(rubric=rubric, bundles=BundleStore())

    async def exercise():
        loop_thread.append(threading.get_ident())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            request = asyncio.create_task(client.post("/scanner/v1/connectivity-check", json=ENDPOINT))
            started = time.monotonic()
            while not entered.is_set():
                await asyncio.sleep(0.005)
            await asyncio.sleep(0.02)
            assert time.monotonic() - started < 0.5
            assert worker_thread[0] != loop_thread[0]
            release.set()
            response = await request
            assert response.status_code == 200
            assert response.json()["success"] is True

    asyncio.run(exercise())


def test_connectivity_handler_closes_runner_on_failure_in_same_thread():
    lifecycle: list[tuple[str, int]] = []

    class FailingRunner:
        def complete(self, _prompt):
            lifecycle.append(("complete", threading.get_ident()))
            raise RunnerError("sanitized failure")

        def close(self):
            lifecycle.append(("close", threading.get_ident()))

    def factory(*_args, **_kwargs):
        lifecycle.append(("create", threading.get_ident()))
        return FailingRunner()

    status, payload = connectivity_check_handler(ENDPOINT, runner_factory=factory)
    assert status == 200
    assert payload == {"success": False, "error": "connection_failed"}
    assert [event for event, _ in lifecycle] == ["create", "complete", "close"]
    assert len({thread_id for _, thread_id in lifecycle}) == 1


def test_scan_runtime_runs_accepted_scan_on_background_worker_thread():
    source = open("api/scan_handlers.py", encoding="utf-8").read()
    assert "threading.Thread(" in source
    assert "target=target" in source
    assert "self._run_accepted_scan_background," in source
