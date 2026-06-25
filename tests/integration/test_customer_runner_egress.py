"""BEAAA-5485 — Customer-runner SSRF regression: 302 redirect to loopback.

Updated under BEAAA-5552 (BEAAA-5527 P1 4th-pass): the runner now sets
``follow_redirects=False`` and rejects 3xx outright via
``RunnerError("customer endpoint returned redirect: 302 ...")`` —
because httpx's auto-redirect path calls ``Response.read()`` on the
intermediate 30x BEFORE following the Location header, which drains
(and *decodes*) the body and bypasses the BEAAA-5532 compression-bomb
gates. The BEAAA-5485 SSRF protection still holds — with no auto-
redirect there is no off-allowlist re-issue to validate; the
transport-layer host check on ``_AllowlistTransport`` fires on the
initial (and only) request. This file now pins both BEAAA-5485 and
the BEAAA-5552 update.

History
-------
Before the BEAAA-5485 fix, the runner issued HTTP via
``urllib.request.urlopen`` — a separate library from the ``httpx``
transport that the allowlist intercepts — so a 302 ``Location:
http://127.0.0.1/`` was followed verbatim, opening an SSRF path from
the customer endpoint into the Prober's loopback surface. BEAAA-5485
moved the runner onto an httpx client with
``ScopedEgressAllowlist`` + ``follow_redirects=True``, so redirects
were intercepted by the transport's host check on every hop. BEAAA-
5552 closed the secondary hazard that ``follow_redirects=True``
required httpx to call ``Response.read()`` on each intermediate 30x —
which decoded compressed bodies into memory before the runner's gates
could see them — by switching to ``follow_redirects=False`` and
adding an outright-rejection 3xx gate ahead of the existing >=400
check.

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
  ``ScopedEgressAllowlist("127.0.0.2")``.

Post-BEAAA-5552 assertion semantic
----------------------------------
Pre-BEAAA-5552 (under ``follow_redirects=True``), this test asserted
that the redirect to ``127.0.0.1`` was refused at the transport-layer
allowlist (a redirect re-issue check). The off-allowlist host never
saw a request because the transport intercepted the redirect.

Post-BEAAA-5552, the redirect is rejected on header receipt by the
new 3xx gate BEFORE any follow-up request is built. The transport-
layer host check would still refuse the off-allowlist target if the
gate were absent — defence-in-depth — but the 3xx gate is what now
fires first. The sentinel handler bound to ``127.0.0.1`` must STILL
not be reached:

* If reached, either (a) the redirect was followed (BEAAA-5552 gate
  regression), or (b) the new 3xx gate was bypassed.
* The flag must remain ``False`` at the end of the run.

The exception type is now narrower: only ``RunnerError`` (raised by
the 3xx gate). ``EgressNotAllowed`` from
``_AllowlistTransport`` is no longer reached on this fixture — there
is no auto-redirect to fire the host check on. The accept-list
``(RunnerError, EgressNotAllowed)`` is preserved so the test remains
auditable against either path, but the assertion message identifies
the BEAAA-5552 path as the expected gate.

A failing run on a pre-BEAAA-5485 tree lets the redirect through and
the flag flips to ``True``; the test surfaces that with a clear
assertion message. A failing run on a tree that drops the BEAAA-5552
gate (e.g. ``follow_redirects=True`` re-introduced) surfaces with the
flag flipping to ``True`` because the BEAAA-5485 transport-layer
check is now the only remaining gate on the redirect — and it *would*
fire on the off-allowlist target, but the test would still see the
flag at ``False`` and the exception at ``EgressNotAllowed``, which is
the regression diagnostic for that case.

Commit ordering
---------------
* BEAAA-5485 PR: file landed FIRST (commit N) and failed against the
  pre-fix tree (``urlopen`` bypassed the allowlist).
* BEAAA-5552 PR: this file updated IN THE SAME COMMIT as the
  ``follow_redirects=False`` fix on ``harness/customer_runner.py``
  (per BEAAA-5552 Discipline bullet 5: "If you split, the
  intermediate state is a CI break"). The pre-existing assertion
  semantic (off-allowlist transport refusal) and the new BEAAA-5552
  semantic (3xx outright refusal) are both satisfied by the post-
  BEAAA-5552 runner.
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
    """BEAAA-5485 AC#1 — runner refuses a 302 to a non-allow-listed host.

    Post-BEAAA-5552 the gate is the 3xx outright-rejection check on
    the runner (``follow_redirects=False``); the BEAAA-5485 transport-
    layer host check is no longer reached because no auto-redirect re-
    issue is built. The accept-list ``(RunnerError, EgressNotAllowed)``
    preserves auditability across the two paths.
    """

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

        # SSRF semantic preserved: the loopback sentinel must NOT have
        # been reached. Under post-BEAAA-5552 the gate is the new 3xx
        # outright-rejection check (the redirect was never followed,
        # so the off-allowlist host never saw a request). Under pre-
        # BEAAA-5552 (follow_redirects=True), the gate would have been
        # the transport-layer host check on _AllowlistTransport (the
        # auto-redirect re-issue would have been refused). Either gate
        # satisfies the SSRF invariant; the failure mode this
        # assertion catches is "redirect was followed AND host check
        # missed it" — the sentinel handler flips the flag.
        assert not _LOOPBACK_HIT["hit"], (
            f"SSRF guard breach: customer_runner followed a 302 redirect to "
            f"{_LOOPBACK_PIVOT_HOST} despite being inside "
            f"ScopedEgressAllowlist({_ALLOWED_BIND_HOST!r}). Exception "
            f"raised after the fact (if any): {exc_info.value!r}"
        )

        # BEAAA-5552 path assertion: the new 3xx outright-rejection
        # gate must be the one that fires. RunnerError is raised with
        # "customer endpoint returned redirect: 302 ..." text, BEFORE
        # any auto-redirect re-issue is built, so the BEAAA-5485
        # transport-layer host check is not reached on this fixture.
        # Cross-references: BEAAA-5485 (transport-layer SSRF check —
        # still active on the INITIAL request, not the redirect re-
        # issue which no longer exists) and BEAAA-5552 (3xx outright
        # rejection — the gate that now fires on this fixture).
        assert isinstance(exc_info.value, RunnerError), (
            f"BEAAA-5552 gate regression: expected RunnerError from the "
            f"3xx outright-rejection gate (the post-BEAAA-5552 path), got "
            f"{type(exc_info.value).__name__}: {exc_info.value!r}. If "
            f"this is EgressNotAllowed, the BEAAA-5552 redirect gate "
            f"is missing and the test fell through to the BEAAA-5485 "
            f"transport-layer host check on the redirect re-issue — "
            f"which means follow_redirects was re-enabled."
        )
        assert "customer endpoint returned redirect" in str(exc_info.value), (
            f"expected the BEAAA-5552 redirect-rejection message; got "
            f"{exc_info.value!r}. Cross-reference BEAAA-5485 (SSRF "
            f"protection on the initial request) + BEAAA-5552 "
            f"(redirect outright-rejection on header receipt). With "
            f"follow_redirects=False the runner never builds a "
            f"redirect re-issue, so the only gate on this fixture is "
            f"the BEAAA-5552 3xx rejection."
        )


__all__: list[str] = []
