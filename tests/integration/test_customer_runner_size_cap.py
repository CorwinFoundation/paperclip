"""BEAAA-5528 (BEAAA-5527 P1) — Customer-runner response cap must stream.

The Track B customer runner
(:func:`harness.customer_runner.customer_agent_http_runner_factory`)
must abort an oversized customer response **before** the full body is
resident in the Prober's memory. Before the BEAAA-5528 fix, the runner
issued the POST via :meth:`httpx.Client.post` which fully buffers the
response body, then sliced ``resp.content[: _MAX_RESPONSE_BYTES + 1]``
— so the cap only fired *after* the entire body had already been
loaded. A malicious or misbehaving customer agent could stream a
multi-GB body and OOM the prober before the cap evaluated.

The fix replaces ``client.post`` with ``client.stream("POST", ...)`` +
``iter_bytes()`` early-break the moment accumulated length exceeds
``_MAX_RESPONSE_BYTES``. This regression pins that behaviour.

Test wire-up
------------
* A short-lived HTTP server is bound to ``127.0.0.2`` on an ephemeral
  port — the same allow-list stand-in used by the BEAAA-5485 SSRF
  regression (a distinct loopback-range string from ``127.0.0.1`` so
  ``ScopedEgressAllowlist`` matches by host string).
* On POST the server replies 200 with
  ``Transfer-Encoding: chunked`` and writes ``_CHUNK_COUNT`` chunks of
  ``_CHUNK_BYTES`` each — total body bytes far in excess of the
  runner's 256 KiB cap. The server sleeps ``_INTER_CHUNK_SLEEP``
  between chunks so TCP backpressure can propagate the client-side
  early-disconnect; a synchronous `wfile.write` in a tight loop on
  loopback would otherwise outrun the disconnect by tens of MiB before
  noticing.
* The server tracks ``bytes_acknowledged_before_disconnect`` — the
  total bytes successfully flushed to the wire before the first
  ``BrokenPipeError`` / ``ConnectionResetError`` from the client side.
  This counter is the authoritative "did the cap fire early?" signal.
* Assertions:

  1. ``runner.complete(...)`` raises :class:`RunnerError` whose message
     matches the existing cap-error shape
     ``"customer endpoint response exceeded ... bytes"``.
  2. ``bytes_acknowledged_before_disconnect`` is strictly less than
     ``_EARLY_ABORT_BYTE_LIMIT`` (4 × the cap). On pre-fix code the
     server completes the full ~32 MiB write before the cap check
     fires — this assertion fails with a clear OOM-hazard message.
     On post-fix code the streaming reader breaks as soon as the
     running length crosses the cap, the client closes the connection,
     and the server observes the broken pipe within a few chunks.

Commit ordering (per BEAAA-5528 AC#1)
-------------------------------------
This file lands FIRST (commit N) and MUST fail against the pre-fix
tree. The fix lands in commit N+1; both are visible in the PR diff so
the bug → fix arc is auditable.
"""
from __future__ import annotations

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Iterable

import pytest

from harness.customer_runner import customer_agent_http_runner_factory
from harness.egress import ScopedEgressAllowlist
from harness.runner import RunnerError


_ALLOWED_BIND_HOST: str = "127.0.0.2"
"""Non-loopback stand-in. Same host as the BEAAA-5485 SSRF regression
to keep the two integration tests using the same allowlist convention."""


_RUNNER_CAP_BYTES: int = 256 * 1024
"""Mirror of ``harness.customer_runner._MAX_RESPONSE_BYTES``. Read by
intent rather than imported so a future cap bump still trips this
test — the streaming-early-abort property is what's pinned, not the
specific byte value."""


_CHUNK_BYTES: int = 32 * 1024
"""Per-chunk payload size. Picked small enough that several chunks fit
inside the cap (cap = 256 KiB = 8 chunks) so the running-length check
has multiple decision points; large enough that 1024 chunks is a
non-trivial body."""


_CHUNK_COUNT: int = 1024
"""Total chunks the server tries to send. 1024 × 32 KiB = 32 MiB —
128× the cap. On pre-fix code, the runner buffers the entire 32 MiB
before the cap check fires; that is the OOM hazard this ticket
closes."""


_INTER_CHUNK_SLEEP_SECONDS: float = 0.005
"""Tiny sleep between chunks. Lets TCP backpressure propagate the
client's early disconnect to the server's next ``wfile.write`` call.
Without this sleep, a fast loopback connection can absorb tens of MiB
of buffered writes before the broken pipe surfaces, masking the
streaming-vs-buffered distinction."""


_EARLY_ABORT_BYTE_LIMIT: int = 4 * _RUNNER_CAP_BYTES
"""Threshold for the "runner aborted early" assertion. 4 × cap = 1 MiB.
On post-fix code the server typically gets a few KiB to a few hundred
KiB past the cap before the broken-pipe surfaces (TCP / chunk-flush
buffering); the 4× margin keeps the test stable across CI hosts.
On pre-fix code the server writes the full 32 MiB before any
client-side disconnect — well above this threshold."""


class _ChunkedOversizedHandler(BaseHTTPRequestHandler):
    """Streams an oversized chunked-encoded body on POST.

    Class state ``bytes_acknowledged_before_disconnect`` accumulates
    bytes successfully flushed before the client closes the connection.
    Reset by the fixture before each test run.
    """

    bytes_acknowledged_before_disconnect: int = 0

    def do_POST(self) -> None:  # noqa: N802 — http.server API
        # Drain the request body so the connection can be reused
        # cleanly under client keep-alive defaults.
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length:
            self.rfile.read(length)

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        cls = type(self)
        chunk = b"x" * _CHUNK_BYTES
        chunk_size_header = f"{_CHUNK_BYTES:x}\r\n".encode("ascii")
        sent = 0
        try:
            for _ in range(_CHUNK_COUNT):
                self.wfile.write(chunk_size_header)
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
                sent += _CHUNK_BYTES
                time.sleep(_INTER_CHUNK_SLEEP_SECONDS)
            # Terminating zero-length chunk.
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            # Expected on post-fix: the streaming reader breaks once the
            # running length crosses the cap, closes the connection,
            # and the next server-side write surfaces a broken pipe.
            pass
        finally:
            cls.bytes_acknowledged_before_disconnect = sent

    def log_message(self, *args, **kwargs) -> None:  # noqa: ARG002
        return  # silence test stdout


def _can_bind(host: str) -> bool:
    """Whether the test environment allows binding to ``host``.

    Mirrors the helper in ``test_customer_runner_egress.py``. Some
    sandboxes do not route 127/8 the way standard Linux does; in those
    environments the test skips rather than reporting a false positive.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, 0))
        return True
    except OSError:
        return False


@pytest.fixture
def chunked_oversized_server() -> Iterable[str]:
    """Yield the customer-endpoint URL of the oversized-chunked server.

    Resets ``bytes_acknowledged_before_disconnect`` before each run so
    the post-test assertion sees only this run's writes. Tears the
    server down at fixture exit even if the test raises.
    """
    if not _can_bind(_ALLOWED_BIND_HOST):
        pytest.skip(
            f"environment cannot bind {_ALLOWED_BIND_HOST}; "
            "skipping streaming-cap regression"
        )

    _ChunkedOversizedHandler.bytes_acknowledged_before_disconnect = 0

    server = HTTPServer((_ALLOWED_BIND_HOST, 0), _ChunkedOversizedHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{_ALLOWED_BIND_HOST}:{port}/probe"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class TestCustomerRunnerStreamsResponseCap:
    """BEAAA-5528 AC#1 — runner streams + early-aborts at the cap."""

    def test_runner_aborts_before_full_body_buffered(
        self, chunked_oversized_server: str
    ) -> None:
        agent_endpoint = chunked_oversized_server
        runner = customer_agent_http_runner_factory(
            agent_endpoint, "fixture-token-not-real-credential"
        )

        with ScopedEgressAllowlist(_ALLOWED_BIND_HOST):
            with pytest.raises(RunnerError) as exc_info:
                runner.complete("oversize-probe-payload")

        # AC#1 line 1: cap error shape preserved.
        assert "customer endpoint response exceeded" in str(exc_info.value), (
            f"runner raised RunnerError but message shape changed; got "
            f"{exc_info.value!r}. Downstream walker error-matching is "
            f"keyed on the substring 'customer endpoint response exceeded'."
        )

        # AC#1 line 2: early abort — server saw < 4× cap before disconnect.
        # On pre-fix code (client.post buffers everything) the server
        # writes the full 32 MiB before the runner's cap check fires;
        # this assertion fails with the OOM-hazard message below.
        sent = _ChunkedOversizedHandler.bytes_acknowledged_before_disconnect
        assert sent < _EARLY_ABORT_BYTE_LIMIT, (
            f"OOM hazard: customer_runner buffered {sent} bytes (>= "
            f"{_EARLY_ABORT_BYTE_LIMIT}) before the {_RUNNER_CAP_BYTES}-"
            f"byte cap aborted the call. The cap fires *after* the body "
            f"has already been loaded into memory — a multi-GB body from "
            f"a misbehaving customer agent will OOM the Prober. Replace "
            f"client.post(...) with client.stream('POST', ...) + "
            f"iter_bytes() early-break on cap."
        )


__all__: list[str] = []
