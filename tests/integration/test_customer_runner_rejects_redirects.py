"""BEAAA-5552 (BEAAA-5527 P1 4th-pass) — customer runner must reject 3xx redirects.

The BEAAA-5532 compression-bomb defences (``Accept-Encoding: identity``,
Content-Encoding response gate, ``iter_raw()``, pre-extension cap
check) hold on the **final** response only. With
``follow_redirects=True`` (the BEAAA-5485 SSRF-era setting at
``harness/customer_runner.py:164``), pinned ``httpx==0.27.2`` enters
``Client._send_handling_redirects`` and calls
:meth:`httpx.Response.read` on every intermediate 30x response BEFORE
following the ``Location`` header. ``Response.read()`` consumes
:meth:`httpx.Response.iter_bytes` — the **decoded-content** iterator.

Result: a malicious-but-allowed endpoint can return

* ``HTTP/1.1 302 Found``
* ``Location: <allowed final URL>``
* ``Content-Encoding: gzip`` + a multi-GB-decoded bomb body

and the bomb is decoded into the Prober's memory before the BEAAA-5532
Content-Encoding gate at ``:194-203`` or the pre-extension cap at
``:215-219`` ever execute. Those gates apply only to the *final*
response. The redirect chain is uncovered.

Reviewer's adopted fix (per BEAAA-5552 AC#1):

1. Set ``follow_redirects=False``.
2. Reject any 3xx response outright with a clear ``RunnerError``.
3. This file is the regression that pins the fix.

This regression is **not** about manually validating redirect Location
targets — the customer endpoint is registered as a single URL; redirect
chains are not part of the contract and adding a manual redirect loop
doubles the security surface for no business benefit. If a future
product requirement requires customer-side redirects, the loop will be
re-introduced under a dedicated co-sign ticket (UW + Founder for the
new surface).

Test cases
----------
* **Case A** — ``RunnerError`` is raised with redirect-rejection text
  (``"customer endpoint returned redirect: 302 ..."``). NOT the
  Content-Encoding gate error, NOT the cap error — the *redirect* gate
  must fire first.
* **Case B** — bomb body is **never** decoded into the runner's buf.
  Verified via the BEAAA-5532 Case C tracking-``bytearray`` idiom:
  subclass ``bytearray`` with an ``extend`` override that records
  ``peak_len`` as a class attribute, inject into
  ``customer_runner_mod.__dict__["bytearray"]`` so LOAD_GLOBAL inside
  the runner resolves to the tracker. ``peak_len == 0`` proves the
  runner raised on header receipt before ``buf = bytearray()`` (and
  therefore before any body iteration) ran. On pre-fix
  (``follow_redirects=True``) httpx silently follows the 302 to GET on
  the same server; the followed-response body lands in the runner's
  ``buf`` and ``peak_len`` moves above 0.
* **Case C** — the test server records
  ``read_bytes_after_response_sent`` on the 302 body-write loop. With
  ``follow_redirects=False`` + immediate 3xx raise inside
  ``with client.stream(...)``, httpx does NOT iterate the body. The
  body-write loop is interrupted at first chunk; cumulative bytes
  written ≤ one chunk. On pre-fix, httpx calls ``Response.read()`` to
  drive the redirect — the server writes the full wire body before
  the client disconnects, so cumulative bytes written ≈ wire size,
  which is sized to be > one chunk so the differentiation is sharp.

Commit ordering (per BEAAA-5552 AC#1)
-------------------------------------
This file lands FIRST (commit N) and MUST fail against the pre-fix
tree (``origin/master@9e88a2e``). The fix lands in commit N+1; both
are visible in the PR diff so the bug -> fix arc is auditable.

Test wire-up
------------
* The redirect server binds to ``127.0.0.2`` on an ephemeral port —
  consistent with BEAAA-5485 / BEAAA-5528 / BEAAA-5532. Both the
  initial allowed host and the redirect's ``Location`` target use
  ``127.0.0.2`` so the BEAAA-5485 transport-layer host check would
  not refuse the redirect (a refusal there would hide the
  redirect-gate regression behind the SSRF gate).
* httpx's redirect machinery downgrades ``POST → GET`` for 302 per
  RFC 7231 §6.4.3 in the absence of a method-preserving 307/308. The
  same server handles ``do_GET`` with a small 200 + JSON body so the
  pre-fix runner reaches the body-iteration loop on the followed
  response — making the peak-bytearray assertion in Case B
  diagnostic (peak_len > 0 on pre-fix; peak_len == 0 on post-fix).

Out of scope
------------
* Manual redirect loop (validate Location against allowlist, cap
  redirect count, close intermediate bodies without reading). If
  product requires customer-side redirects, file dedicated co-sign
  ticket with UW + Founder for the expanded surface (BEAAA-5552
  description "Out of scope").
* Changing ``_MAX_RESPONSE_BYTES`` (256 KiB stays).
"""
from __future__ import annotations

import gzip
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Iterable

import pytest

import harness.customer_runner as customer_runner_mod
from harness.customer_runner import customer_agent_http_runner_factory
from harness.egress import ScopedEgressAllowlist
from harness.runner import RunnerError


_ALLOWED_BIND_HOST: str = "127.0.0.2"
"""Customer-endpoint allowed host. Same convention as BEAAA-5485 /
BEAAA-5528 / BEAAA-5532. The redirect target also resolves to this
host so the BEAAA-5485 transport-layer host check would not refuse the
redirect — isolating the redirect-rejection gate from the SSRF gate."""


_GZIP_BOMB_INFLATED_BYTES: int = 256 * 1024 * 1024
"""Decompressed bomb size. 256 MiB inflated → ~256 KiB on the wire
(zero bytes compress ~1000:1 under DEFLATE). The wire size is
deliberately ≫ ``_BYTES_ACK_CEILING`` (64 KiB) so Case C's
"interrupted within four chunks" assertion sharply differentiates:
on pre-fix httpx calls ``Response.read()`` to drive the redirect and
the full ~256 KiB wire is drained, on post-fix the runner raises on
header receipt and the server's body-write loop is torn down within
``_BYTES_ACK_CEILING`` bytes via the inter-chunk sleep idiom."""


_CHUNK_BYTES: int = 16 * 1024
"""Server-side write chunk size. Matches the BEAAA-5532 Case C
convention so the 'within one chunk' bound is consistent across the
two regressions."""


_INTER_CHUNK_SLEEP_SECONDS: float = 0.005
"""Per-chunk sleep between server-side writes. Matches the BEAAA-5528
streaming-cap idiom so the FIN from the client-side disconnect has
time to propagate before the next write. Without this sleep a tight
``wfile.write`` loop on loopback fills the kernel TCP send buffer
(default 64 KiB-4 MiB) before the disconnect is observed, masking the
"body never drained" signal Case C is testing. With this sleep the
server is idle ~99% of the time, the FIN arrives during the first or
second sleep, and the next ``wfile.flush`` surfaces as BrokenPipeError
within ``_BYTES_ACK_CEILING`` bytes."""


_BYTES_ACK_CEILING: int = 4 * _CHUNK_BYTES
"""Upper bound on bytes the server should flush before the client-
side disconnect propagates, on the post-fix path. Slack budget = 4
chunks (64 KiB) — the same shape as BEAAA-5528's
``_EARLY_ABORT_BYTE_LIMIT = 4 * _RUNNER_CAP_BYTES``: enough headroom
for kernel TCP send-buffer behaviour on Linux loopback, tight enough
that pre-fix code (which fully drains the wire body via httpx's
``Response.read()`` to drive the redirect) trips the assertion."""


def _build_gzip_bomb() -> bytes:
    """Return a gzip-encoded payload that inflates to ``_GZIP_BOMB_INFLATED_BYTES``.

    A run of ``\\0`` bytes compresses ~1000:1 under DEFLATE, so the
    wire payload is on the order of tens of KiB. Generated once per
    test process inside the fixture; never written to disk.
    """
    return gzip.compress(b"\x00" * _GZIP_BOMB_INFLATED_BYTES, compresslevel=9)


def _can_bind(host: str) -> bool:
    """Whether the test environment allows binding to ``host``."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, 0))
        return True
    except OSError:
        return False


class _RedirectGzipBombHandler(BaseHTTPRequestHandler):
    """302 + gzip + bombed body on POST; small 200 + JSON body on GET.

    POST is the initial wire shape from the runner. The 302 carries
    ``Location: <same-host>/followed`` so the BEAAA-5485 transport-
    layer host check is not the gate under test. ``Content-Encoding:
    gzip`` + a multi-MB-decoded body is the bomb the runner must NOT
    decode. httpx's redirect machinery downgrades 302 + POST → GET, so
    the followed request lands on ``do_GET``, which returns a small
    JSON body that lands in the runner's ``buf`` on the pre-fix tree
    (making peak_len > 0).

    Class attributes track wire-side state so test assertions can
    drive on them:

    * ``bytes_acknowledged_before_disconnect`` — cumulative bytes
      written to the wfile during the 302's body loop before the
      client tears down the connection. On post-fix the runner raises
      on header receipt and the server's first or second flush hits
      BrokenPipeError; on pre-fix httpx reads the full body to drive
      the redirect.
    * ``post_request_count`` and ``get_request_count`` — diagnostic
      counts so a test failure can name "redirect was followed" vs
      "redirect was rejected".
    """

    bomb_bytes: bytes = b""
    chunk_bytes: int = _CHUNK_BYTES
    location_url: str = ""
    bytes_acknowledged_before_disconnect: int = 0
    post_request_count: int = 0
    get_request_count: int = 0

    def do_POST(self) -> None:  # noqa: N802 — http.server API
        cls = type(self)
        cls.post_request_count += 1
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length:
            self.rfile.read(length)
        body = cls.bomb_bytes
        self.send_response(302)
        self.send_header("Location", cls.location_url)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        # Stream the body in small chunks with explicit flush so we
        # can detect when the client closed the connection. The
        # post-fix runner raises immediately on the 3xx status (before
        # any body iteration); httpx tears down the connection at the
        # next context exit, and the next flush here surfaces as a
        # BrokenPipeError — bytes_acknowledged stops at < one chunk.
        # On pre-fix, httpx calls Response.read() to drive the
        # redirect; the full wire body is drained and bytes_
        # acknowledged ≈ wire size (much greater than one chunk).
        sent = 0
        chunk = cls.chunk_bytes
        try:
            for offset in range(0, len(body), chunk):
                piece = body[offset:offset + chunk]
                self.wfile.write(piece)
                self.wfile.flush()
                sent += len(piece)
                # Inter-chunk sleep (BEAAA-5528 idiom) so the FIN from
                # the client disconnect propagates between chunks. On
                # loopback a tight write loop fills the kernel send
                # buffer before the disconnect is observed; the sleep
                # makes the buffer steady-state empty, the FIN arrives
                # mid-stream, and the next write surfaces as
                # BrokenPipeError. Without this, ``sent`` would record
                # the full wire body on both pre-fix and post-fix paths
                # and the regression would lose its sharpness.
                time.sleep(_INTER_CHUNK_SLEEP_SECONDS)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            cls.bytes_acknowledged_before_disconnect = sent

    def do_GET(self) -> None:  # noqa: N802 — http.server API
        cls = type(self)
        cls.get_request_count += 1
        # Small identity-encoded JSON body — well under the runner's
        # 256 KiB cap. On pre-fix code (follow_redirects=True) the
        # 302's gzip-bombed body is decoded inside httpx during the
        # redirect handling, and the followed GET lands here. The
        # runner enters its body-iteration loop on this 200, so the
        # tracking ``bytearray`` records peak_len > 0 — the signal
        # the post-fix path must drive to 0 by rejecting the 302
        # before the buf loop runs.
        body = b'{"response": "followed-redirect"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def log_message(self, *args, **kwargs) -> None:  # noqa: ARG002
        return  # silence test stdout


@pytest.fixture
def redirect_gzip_bomb_server() -> Iterable[str]:
    """Yield the customer-endpoint URL for the redirect + gzip-bomb server."""
    if not _can_bind(_ALLOWED_BIND_HOST):
        pytest.skip(
            f"environment cannot bind {_ALLOWED_BIND_HOST}; "
            "skipping redirect-rejection regression"
        )
    _RedirectGzipBombHandler.bomb_bytes = _build_gzip_bomb()
    _RedirectGzipBombHandler.bytes_acknowledged_before_disconnect = 0
    _RedirectGzipBombHandler.post_request_count = 0
    _RedirectGzipBombHandler.get_request_count = 0
    server = HTTPServer((_ALLOWED_BIND_HOST, 0), _RedirectGzipBombHandler)
    port = server.server_address[1]
    # Same-host Location so the BEAAA-5485 transport-layer host check
    # is not the gate under test. Different path so it's unambiguous
    # in server-side request counting that the follow-up request hit
    # the redirect target, not the initial endpoint.
    _RedirectGzipBombHandler.location_url = (
        f"http://{_ALLOWED_BIND_HOST}:{port}/followed-redirect-target"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{_ALLOWED_BIND_HOST}:{port}/probe"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class _TrackingBytearray(bytearray):
    """Bytearray subclass that records its peak length across extends.

    Reused BEAAA-5532 Case C idiom verbatim (per BEAAA-5552 Discipline
    bullet 3). Injected into the runner module's globals so
    ``buf = bytearray()`` inside ``_CustomerAgentHttpRunner.complete``
    resolves via LOAD_GLOBAL to this subclass rather than the builtin.
    """

    peak_len: int = 0

    @classmethod
    def reset(cls) -> None:
        cls.peak_len = 0

    def extend(self, data) -> None:  # type: ignore[override]
        super().extend(data)
        if len(self) > type(self).peak_len:
            type(self).peak_len = len(self)


class TestCustomerRunnerRejectsRedirects:
    """BEAAA-5552 AC#1 — runner rejects 3xx outright, body never decoded."""

    def test_runner_rejects_302_before_body_decode(
        self, redirect_gzip_bomb_server: str
    ) -> None:
        agent_endpoint = redirect_gzip_bomb_server
        runner = customer_agent_http_runner_factory(
            agent_endpoint, "fixture-token-not-real-credential"
        )

        # Inject the tracking-bytearray into runner module globals so
        # ``buf = bytearray()`` resolves to the tracker if the code
        # path ever reaches the body-iteration loop. peak_len == 0 at
        # the end of the call is the unambiguous "buf loop was never
        # entered" signal — i.e. the runner raised on header receipt
        # before the followed-response body could land in buf.
        _TrackingBytearray.reset()
        original = customer_runner_mod.__dict__.get("bytearray")
        customer_runner_mod.__dict__["bytearray"] = _TrackingBytearray
        try:
            with ScopedEgressAllowlist(_ALLOWED_BIND_HOST):
                with pytest.raises(RunnerError) as exc_info:
                    runner.complete("redirect-rejection-probe-payload")
        finally:
            if original is None:
                customer_runner_mod.__dict__.pop("bytearray", None)
            else:
                customer_runner_mod.__dict__["bytearray"] = original

        msg = str(exc_info.value)

        # Case A — the runner must surface the 3xx as a redirect-
        # rejection error. NOT the Content-Encoding gate, NOT the cap
        # error. On pre-fix (follow_redirects=True) httpx silently
        # follows the 302 (after calling Response.read() — which
        # inflates the bomb via iter_bytes) and the followed GET
        # response lands at /followed-redirect-target with a 200; the
        # runner returns cleanly and pytest.raises ITSELF fails before
        # this assertion is reached. Either way, this assertion fails
        # on pre-fix.
        assert "customer endpoint returned redirect" in msg, (
            f"expected RunnerError to identify the 3xx response as a "
            f"redirect-rejection; got {exc_info.value!r}. On pre-fix "
            f"code (follow_redirects=True) httpx calls Response.read() "
            f"— the decoded-content path — on the intermediate 302 "
            f"BEFORE following the Location header, which inflates the "
            f"{_GZIP_BOMB_INFLATED_BYTES}-byte bomb into memory. The "
            f"redirect gate must reject 3xx on header receipt so the "
            f"body iterator is never touched."
        )
        assert "302" in msg, (
            f"RunnerError must name the offending status code so "
            f"operator triage can identify the misbehaving customer "
            f"endpoint; got {exc_info.value!r}"
        )

        # Case B — runner's buf loop must never run. On pre-fix the
        # 302 is followed to GET /followed-redirect-target, the 200
        # body lands in the runner's buf, and peak_len > 0. On post-
        # fix the runner raises on the 3xx BEFORE ``buf = bytearray()``
        # is executed; the tracking subclass is never instantiated,
        # peak_len remains at the class-attribute default of 0.
        peak = _TrackingBytearray.peak_len
        assert peak == 0, (
            f"redirect-follow leak: runner's body-buffer reached "
            f"{peak} bytes after a 302 response. The runner must raise "
            f"on the 3xx status BEFORE the body-iteration loop runs — "
            f"peak_len must remain at the class-attribute default of "
            f"0. On pre-fix code (follow_redirects=True) httpx follows "
            f"the redirect after inflating the bomb via Response.read(), "
            f"the followed GET lands at /followed-redirect-target, and "
            f"the followed-response body lands in the runner's buf."
        )

        # Case B follow-up — the redirect must not have been followed
        # at all. get_request_count counts requests on
        # /followed-redirect-target server-side. Pre-fix: 1. Post-fix:
        # 0. (post_request_count is 1 in both cases — the initial
        # POST.)
        get_hits = _RedirectGzipBombHandler.get_request_count
        post_hits = _RedirectGzipBombHandler.post_request_count
        assert get_hits == 0, (
            f"redirect was followed: the server received {get_hits} "
            f"GET request(s) on /followed-redirect-target after the "
            f"runner saw the 302. The runner must reject 3xx outright; "
            f"no follow-up request must be issued. POST hits: "
            f"{post_hits}; GET hits: {get_hits}."
        )

        # Case C — server's body-write loop must be interrupted at
        # first chunk. On pre-fix httpx calls Response.read() to drive
        # the redirect, which drains the full bomb wire (multiple
        # chunks); bytes_acknowledged ≈ len(bomb_bytes) > _CHUNK_BYTES.
        # On post-fix the runner raises on the 3xx and the context
        # manager exit tears down the connection; the server's wfile
        # flush surfaces as BrokenPipeError within one chunk.
        sent = _RedirectGzipBombHandler.bytes_acknowledged_before_disconnect
        assert sent < _BYTES_ACK_CEILING, (
            f"redirect body drain: server flushed {sent} bytes on the "
            f"302 response before client disconnect (>= "
            f"{_BYTES_ACK_CEILING} = 4 × _CHUNK_BYTES). With "
            f"follow_redirects=False + immediate 3xx raise, the runner "
            f"must close the connection on the next context exit "
            f"without iterating the body. The inter-chunk sleep (5 ms) "
            f"lets the FIN propagate, so anything >= the four-chunk "
            f"ceiling indicates httpx drained the body — the failure "
            f"mode this regression closes. On pre-fix code httpx "
            f"calls Response.read() to drive the redirect, draining "
            f"the full ~256 KiB wire."
        )


__all__: list[str] = []
