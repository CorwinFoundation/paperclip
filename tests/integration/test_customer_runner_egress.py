"""BEAAA-5485 — Customer-runner SSRF regression: 302 redirect to loopback.

The Track B customer runner
(:func:`harness.customer_runner.customer_agent_http_runner_factory`)
must refuse a redirect target that falls outside the active
:class:`harness.egress.ScopedEgressAllowlist`. Before the BEAAA-5485
fix, the runner issued HTTP via ``urllib.request.urlopen`` — a separate
library from the ``httpx`` transport that the allowlist intercepts —
so a 302 ``Location: http://127.0.0.1/`` was followed verbatim, opening
an SSRF path from the customer endpoint into the Prober's loopback
surface.

This regression pins the fix.

Test wire-up
------------
* A short-lived HTTP server is bound to ``127.0.0.2`` on an ephemeral
  port — a different *host string* from ``127.0.0.1`` for the
  allowlist's purposes, even though both live in the 127.0.0.0/8
  loopback range. The allowlist matches on host strings, not IP
  ranges, so ``127.0.0.2`` is the stand-in for the customer's declared
  non-loopback endpoint host; ``127.0.0.1`` is the disallowed
  loopback-pivot target.
* The fake target responds with HTTP 302 + ``Location:
  http://127.0.0.1/``.
* The test wraps the runner call in
  ``ScopedEgressAllowlist("127.0.0.2")``. The fix forces the runner
  through the allowlist's transport (or its own equivalent host-scoped
  transport), so the redirect re-issue to ``127.0.0.1`` fires the
  transport check and refuses the request before any bytes hit the
  loopback service.
* The assertion is that the call raises (or otherwise refuses) without
  ever touching ``127.0.0.1`` — verified by binding a sentinel handler
  to ``127.0.0.1`` that flips an in-process flag if it is ever
  reached. The flag must remain ``False`` at the end of the run.

A failing run (pre-fix) lets the redirect through and the flag flips
to ``True``; the test surfaces that with a clear assertion message.

Commit ordering (per BEAAA-5485 AC#1)
-------------------------------------
This file lands FIRST in the BEAAA-5485 PR (commit N) and MUST fail
against the pre-fix tree. The fix lands in commit N+1; both are
visible in the PR diff so the bug → fix arc is auditable.
"""
from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Iterable, Tuple

import pytest

from harness.customer_runner import customer_agent_http_runner_factory
from harness.egress import EgressNotAllowed, ScopedEgressAllowlist
from harness.runner import RunnerError


_ALLOWED_BIND_HOST: str = "127.0.0.2"
"""Non-loopback stand-in. The allowlist matches on host strings, so a
distinct loopback-range address serves as the customer's declared
endpoint host without requiring an external interface in CI."""


_LOOPBACK_PIVOT_HOST: str = "127.0.0.1"
"""Target of the 302 redirect the runner must refuse."""


# In-process flag flipped if the runner ever reaches the loopback
# sentinel — the unambiguous "SSRF guard failed" signal.
_LOOPBACK_HIT: dict[str, bool] = {"hit": False}


class _RedirectToLoopbackHandler(BaseHTTPRequestHandler):
    """Returns 302 ``Location: http://127.0.0.1:<sentinel-port>/`` on POST.

    The Location target points at the in-process loopback sentinel so
    we get a deterministic positive signal if the redirect *is*
    followed (instead of relying on whether something happens to be
    listening on a well-known loopback port).
    """

    sentinel_port: int = 0  # set by the fixture before requests land

    def do_POST(self) -> None:  # noqa: N802 — http.server API
        # Drain the body so the connection can close cleanly under
        # client-side connection reuse.
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length:
            self.rfile.read(length)
        target = f"http://{_LOOPBACK_PIVOT_HOST}:{type(self).sentinel_port}/"
        self.send_response(302)
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args, **kwargs) -> None:  # noqa: ARG002
        return  # silence test stdout


class _LoopbackSentinelHandler(BaseHTTPRequestHandler):
    """Sentinel handler bound to 127.0.0.1 — flips the SSRF flag.

    Reached only if the redirect is followed. The flag is the
    authoritative pass/fail signal; reaching this handler means the
    egress guard did not refuse the loopback pivot.
    """

    def do_GET(self) -> None:  # noqa: N802
        _LOOPBACK_HIT["hit"] = True
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        _LOOPBACK_HIT["hit"] = True
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args, **kwargs) -> None:  # noqa: ARG002
        return


def _can_bind(host: str) -> bool:
    """Whether the test environment allows binding to ``host``.

    127.0.0.2 is bind-able under Linux's reserved 127.0.0.0/8 loopback
    range; environments that route 127/8 differently (some sandboxes)
    are detected and skip the test rather than reporting a false
    positive.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, 0))
        return True
    except OSError:
        return False


@pytest.fixture
def redirect_server() -> Iterable[Tuple[str, int]]:
    """Yield (redirect_endpoint_url, sentinel_port).

    Two servers are started in this fixture:

    * The redirect server on ``127.0.0.2:<ephemeral>``. Its 302
      ``Location`` points at the sentinel.
    * The sentinel server on ``127.0.0.1:<ephemeral>``. Reaching it
      flips ``_LOOPBACK_HIT["hit"]`` to ``True``.

    Both are torn down at fixture exit even if the test raises.
    """
    _LOOPBACK_HIT["hit"] = False

    if not _can_bind(_ALLOWED_BIND_HOST):
        pytest.skip(
            f"environment cannot bind {_ALLOWED_BIND_HOST}; "
            "skipping SSRF regression"
        )

    sentinel = HTTPServer((_LOOPBACK_PIVOT_HOST, 0), _LoopbackSentinelHandler)
    sentinel_port = sentinel.server_address[1]
    sentinel_thread = threading.Thread(target=sentinel.serve_forever, daemon=True)
    sentinel_thread.start()

    _RedirectToLoopbackHandler.sentinel_port = sentinel_port

    redirect = HTTPServer((_ALLOWED_BIND_HOST, 0), _RedirectToLoopbackHandler)
    redirect_port = redirect.server_address[1]
    redirect_thread = threading.Thread(target=redirect.serve_forever, daemon=True)
    redirect_thread.start()

    try:
        yield (
            f"http://{_ALLOWED_BIND_HOST}:{redirect_port}/probe",
            sentinel_port,
        )
    finally:
        redirect.shutdown()
        redirect.server_close()
        redirect_thread.join(timeout=2)
        sentinel.shutdown()
        sentinel.server_close()
        sentinel_thread.join(timeout=2)


class TestCustomerRunnerRefusesLoopbackRedirect:
    """BEAAA-5485 AC#1 — runner refuses a 302 to a non-allow-listed host."""

    def test_runner_refuses_loopback_pivot_via_redirect(
        self, redirect_server: Tuple[str, int]
    ) -> None:
        agent_endpoint, _sentinel_port = redirect_server
        runner = customer_agent_http_runner_factory(
            agent_endpoint, "fixture-token-not-real-credential"
        )

        with ScopedEgressAllowlist(_ALLOWED_BIND_HOST):
            with pytest.raises((RunnerError, EgressNotAllowed)) as exc_info:
                runner.complete("ssrf-probe-payload")

        assert not _LOOPBACK_HIT["hit"], (
            f"SSRF guard breach: customer_runner followed a 302 redirect to "
            f"{_LOOPBACK_PIVOT_HOST} despite being inside "
            f"ScopedEgressAllowlist({_ALLOWED_BIND_HOST!r}). Exception "
            f"raised after the fact (if any): {exc_info.value!r}"
        )


__all__: list[str] = []
