"""BEAAA-5532 (BEAAA-5527 P1 3rd-pass) — compression-bomb residual on the customer runner.

The Track B customer runner
(:func:`harness.customer_runner.customer_agent_http_runner_factory`)
closed the plain-chunked-body OOM hazard in BEAAA-5528 by switching from
``client.post(...)`` to ``client.stream("POST", ...)`` +
``iter_bytes()`` early-break. Reviewer's 3rd pass on
``origin/master@d372283`` (comment ``f641e462`` on parent BEAAA-5527
14:51:12Z) identified a compression-bomb residual:

* The runner builds the request headers WITHOUT ``Accept-Encoding``.
  httpx's default is ``gzip, deflate, br, zstd`` — the customer endpoint
  is *invited* to compress.
* ``iter_bytes()`` is httpx's **decoded-content** iterator: a small
  gzip / zstd wire payload (e.g. 100 KB) can decompress to multi-GB
  before the cap check fires on the **inflated** bytes in memory. The
  cap effectively does not protect against a compression bomb.
* Even on identity, ``buf.extend(chunk)`` runs BEFORE the cap check, so
  one chunk-worth of slack lives in memory before the break.

This file pins the three reviewer-suggested fix properties (AC#1 line
1–3 of BEAAA-5532):

* **Case A** — server returns ``Content-Encoding: gzip`` with a
  gzip-bombed body. The runner must REFUSE the response with a
  ``RunnerError`` whose message identifies the unsupported encoding
  *before* any decoding takes place. The bombed body must never be
  inflated into the Prober's memory.
* **Case B** — server captures the inbound ``Accept-Encoding`` request
  header. The runner must send ``Accept-Encoding: identity`` so a
  well-behaved customer endpoint is invited to return uncompressed
  bytes.
* **Case C** — server streams ``_MAX_RESPONSE_BYTES + 1024`` identity-
  encoded bytes. The runner must raise the existing cap error AND the
  in-memory peak buffer length must stay within
  ``_MAX_RESPONSE_BYTES + ONE_CHUNK_SIZE`` — verified by injecting a
  tracking ``bytearray`` subclass into the runner module's globals so
  ``buf = bytearray()`` resolves to the tracker. The pre-extension cap
  check (``if len(buf) + len(chunk) > cap: break`` BEFORE
  ``buf.extend(chunk)``) eliminates the post-extension one-chunk
  overrun the BEAAA-5528 fix still permitted.

Commit ordering (per BEAAA-5532 AC#1)
-------------------------------------
This file lands FIRST (commit N) and MUST fail against the pre-fix
tree (``origin/master@d372283``). The fix lands in commit N+1; both
are visible in the PR diff so the bug -> fix arc is auditable.

Test wire-up
------------
* All three cases bind a short-lived HTTP server to ``127.0.0.2`` on
  an ephemeral port — the same allow-list stand-in convention used by
  the BEAAA-5485 SSRF and BEAAA-5528 size-cap regressions, so the
  three integration tests share host-string semantics inside
  :class:`harness.egress.ScopedEgressAllowlist`.
* Each case scopes its server lifecycle to a dedicated pytest fixture
  so a failure in one case can't leak state into another.

Out of scope
------------
* Decoding ``gzip`` / ``zstd`` / ``br`` / ``deflate``. The reviewer's
  fix REJECTS encoded responses; it does not negotiate them. The
  rationale: a customer endpoint that ignores ``Accept-Encoding:
  identity`` is misbehaving and we surface the misbehaviour rather
  than silently inflating the body.
* Bumping ``_MAX_RESPONSE_BYTES``. The 256 KiB ceiling is unchanged.
"""
from __future__ import annotations

import gzip
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Iterable

import pytest

import harness.customer_runner as customer_runner_mod
from harness.customer_runner import customer_agent_http_runner_factory
from harness.egress import ScopedEgressAllowlist
from harness.runner import RunnerError


_ALLOWED_BIND_HOST: str = "127.0.0.2"
"""Non-loopback stand-in. Same host string as BEAAA-5485 / BEAAA-5528
keeps the allow-list convention consistent across integration tests."""


_RUNNER_CAP_BYTES: int = 256 * 1024
"""Mirror of ``harness.customer_runner._MAX_RESPONSE_BYTES``. Read by
intent rather than imported so a future cap bump still trips the
streaming-early-abort property pinned by this test."""


_ONE_CHUNK_SIZE: int = 64 * 1024
"""Upper-bound assumption on the size of a single chunk yielded by
``httpx.Response.iter_raw``. Used as the slack budget in the peak-
buffer assertion for Case C. 64 KiB matches the default network-recv
buffer httpx falls back to when no explicit ``chunk_size`` is
requested; the assertion holds for any value where the cap check
fires before more than one chunk lands in the buffer."""


def _can_bind(host: str) -> bool:
    """Whether the test environment allows binding to ``host``.

    Mirrors the helper in the sibling integration tests. Sandboxes
    that do not route 127/8 the way standard Linux does skip rather
    than report a false positive.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, 0))
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Case A — server replies Content-Encoding: gzip with a bombed body.
# ---------------------------------------------------------------------------


_GZIP_BOMB_INFLATED_BYTES: int = 8 * 1024 * 1024
"""Decompressed bomb size. 8 MiB inflated from a ~8 KiB wire payload —
small enough that the test runs fast, large enough to dwarf the
256 KiB cap by a factor of 32. If the runner ever DID decode this
payload it would either (a) overrun the cap by 32x of inflated bytes
or (b) at minimum surface a 'response exceeded' error keyed on the
inflated byte stream rather than the unsupported-encoding error this
test asserts."""


def _build_gzip_bomb() -> bytes:
    """Return a gzip-encoded payload that inflates to ``_GZIP_BOMB_INFLATED_BYTES``.

    A run of ``\\0`` bytes compresses ~1000:1 under DEFLATE, so the wire
    payload is on the order of a few KB. Generated once per test
    process inside the fixture; never written to disk.
    """
    return gzip.compress(b"\x00" * _GZIP_BOMB_INFLATED_BYTES, compresslevel=9)


class _GzipBombHandler(BaseHTTPRequestHandler):
    """Replies 200 ``Content-Encoding: gzip`` with the gzip bomb body.

    The server reports ``Content-Encoding: gzip`` regardless of the
    client's ``Accept-Encoding`` so the runner's defence-in-depth
    Content-Encoding gate fires even against a non-compliant customer
    endpoint that ignores ``Accept-Encoding: identity``.
    """

    bomb_bytes: bytes = b""

    def do_POST(self) -> None:  # noqa: N802 — http.server API
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length:
            self.rfile.read(length)
        body = type(self).bomb_bytes
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            # Expected when the runner refuses the encoding and closes
            # the stream context before reading the full body.
            pass

    def log_message(self, *args, **kwargs) -> None:  # noqa: ARG002
        return  # silence test stdout


@pytest.fixture
def gzip_bomb_server() -> Iterable[str]:
    """Yield the customer-endpoint URL for the gzip-bomb server."""
    if not _can_bind(_ALLOWED_BIND_HOST):
        pytest.skip(
            f"environment cannot bind {_ALLOWED_BIND_HOST}; "
            "skipping compression-bomb regression"
        )
    _GzipBombHandler.bomb_bytes = _build_gzip_bomb()
    server = HTTPServer((_ALLOWED_BIND_HOST, 0), _GzipBombHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{_ALLOWED_BIND_HOST}:{port}/probe"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class TestCustomerRunnerRefusesCompressionBomb:
    """BEAAA-5532 AC#1 Case A — Content-Encoding != identity is rejected."""

    def test_runner_refuses_gzip_response_before_decoding(
        self, gzip_bomb_server: str
    ) -> None:
        agent_endpoint = gzip_bomb_server
        runner = customer_agent_http_runner_factory(
            agent_endpoint, "fixture-token-not-real-credential"
        )

        with ScopedEgressAllowlist(_ALLOWED_BIND_HOST):
            with pytest.raises(RunnerError) as exc_info:
                runner.complete("compression-bomb-probe-payload")

        msg = str(exc_info.value)
        # The fix must REJECT — not the cap error. On pre-fix code the
        # runner happily decodes the gzip stream via iter_bytes() and
        # surfaces the inflated bytes through the cap path, producing
        # "customer endpoint response exceeded ... bytes" instead.
        assert "unsupported Content-Encoding" in msg, (
            f"expected RunnerError to identify the unsupported "
            f"Content-Encoding before decoding the bomb; got "
            f"{exc_info.value!r}. On pre-fix code the runner would "
            f"have called iter_bytes() (the decoded-content iterator) "
            f"and inflated up to {_GZIP_BOMB_INFLATED_BYTES} bytes "
            f"into memory before the cap check fired — the OOM hazard "
            f"the Content-Encoding gate closes."
        )
        assert "gzip" in msg.lower(), (
            f"RunnerError must name the offending encoding so operator "
            f"triage can identify the misbehaving customer endpoint; "
            f"got {exc_info.value!r}"
        )


# ---------------------------------------------------------------------------
# Case B — request must carry Accept-Encoding: identity.
# ---------------------------------------------------------------------------


class _CaptureAcceptEncodingHandler(BaseHTTPRequestHandler):
    """Captures the inbound ``Accept-Encoding`` header and replies trivially.

    Records the header value on a class attribute so the test can
    assert against it after the runner call returns. The reply is a
    tiny ``{"response": "ok"}`` JSON body well under the cap so the
    runner returns cleanly.
    """

    captured_accept_encoding: str | None = None

    def do_POST(self) -> None:  # noqa: N802 — http.server API
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length:
            self.rfile.read(length)
        type(self).captured_accept_encoding = self.headers.get(
            "Accept-Encoding"
        )
        body = b'{"response": "ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def log_message(self, *args, **kwargs) -> None:  # noqa: ARG002
        return


@pytest.fixture
def accept_encoding_capture_server() -> Iterable[str]:
    """Yield the customer-endpoint URL for the Accept-Encoding capture server."""
    if not _can_bind(_ALLOWED_BIND_HOST):
        pytest.skip(
            f"environment cannot bind {_ALLOWED_BIND_HOST}; "
            "skipping Accept-Encoding header regression"
        )
    _CaptureAcceptEncodingHandler.captured_accept_encoding = None
    server = HTTPServer(
        (_ALLOWED_BIND_HOST, 0), _CaptureAcceptEncodingHandler
    )
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{_ALLOWED_BIND_HOST}:{port}/probe"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class TestCustomerRunnerSendsIdentityAcceptEncoding:
    """BEAAA-5532 AC#1 Case B — request advertises identity-only."""

    def test_runner_sends_accept_encoding_identity(
        self, accept_encoding_capture_server: str
    ) -> None:
        agent_endpoint = accept_encoding_capture_server
        runner = customer_agent_http_runner_factory(
            agent_endpoint, "fixture-token-not-real-credential"
        )
        with ScopedEgressAllowlist(_ALLOWED_BIND_HOST):
            result = runner.complete("accept-encoding-probe-payload")

        assert result == "ok", (
            f"runner should return the JSON 'response' field on a "
            f"well-formed 200; got {result!r}"
        )
        captured = _CaptureAcceptEncodingHandler.captured_accept_encoding
        # On pre-fix code the headers dict omits Accept-Encoding, so
        # httpx defaults to "gzip, deflate, br, zstd" and that value
        # lands on the server. On post-fix code the runner pins the
        # header to "identity" so a well-behaved customer endpoint
        # returns uncompressed bytes.
        assert captured == "identity", (
            f"expected the runner to advertise Accept-Encoding: identity "
            f"so a well-behaved customer endpoint returns uncompressed "
            f"bytes; got Accept-Encoding={captured!r}. On pre-fix code "
            f"the runner inherited httpx's default "
            f"'gzip, deflate, br, zstd', inviting a compression-bomb "
            f"residual that the Content-Encoding gate alone cannot "
            f"fully prevent (a customer endpoint that respects "
            f"Accept-Encoding will not encode in the first place)."
        )


# ---------------------------------------------------------------------------
# Case C — pre-extension cap check eliminates post-extension overrun.
# ---------------------------------------------------------------------------


class _IdentityOversizeHandler(BaseHTTPRequestHandler):
    """Sends ``_RUNNER_CAP_BYTES + 1024`` bytes of identity-encoded body.

    Uses ``Transfer-Encoding: chunked`` with small chunks so the runner
    sees multiple iterator turns and the pre-extension cap check has a
    distinct decision point at the boundary. Tracks bytes successfully
    flushed before the first broken-pipe so the test can sanity-check
    that the runner did not consume the full overshoot.
    """

    bytes_acknowledged_before_disconnect: int = 0
    chunk_bytes: int = 16 * 1024  # small enough to land multiple at the cap
    total_bytes: int = _RUNNER_CAP_BYTES + 1024

    def do_POST(self) -> None:  # noqa: N802 — http.server API
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length:
            self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        cls = type(self)
        sent = 0
        try:
            remaining = cls.total_bytes
            while remaining > 0:
                size = min(cls.chunk_bytes, remaining)
                self.wfile.write(f"{size:x}\r\n".encode("ascii"))
                self.wfile.write(b"y" * size)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
                sent += size
                remaining -= size
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            cls.bytes_acknowledged_before_disconnect = sent

    def log_message(self, *args, **kwargs) -> None:  # noqa: ARG002
        return


@pytest.fixture
def identity_oversize_server() -> Iterable[str]:
    """Yield the customer-endpoint URL for the identity-oversize server."""
    if not _can_bind(_ALLOWED_BIND_HOST):
        pytest.skip(
            f"environment cannot bind {_ALLOWED_BIND_HOST}; "
            "skipping pre-extension cap regression"
        )
    _IdentityOversizeHandler.bytes_acknowledged_before_disconnect = 0
    server = HTTPServer((_ALLOWED_BIND_HOST, 0), _IdentityOversizeHandler)
    port = server.server_address[1]
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

    Injected into the ``harness.customer_runner`` module's globals so
    ``buf = bytearray()`` inside ``_CustomerAgentHttpRunner.complete``
    resolves via LOAD_GLOBAL to this subclass rather than the builtin.
    The peak-length counter survives the bytearray's lifetime via a
    class attribute the test reads after the runner returns.
    """

    peak_len: int = 0

    @classmethod
    def reset(cls) -> None:
        cls.peak_len = 0

    def extend(self, data) -> None:  # type: ignore[override]
        super().extend(data)
        if len(self) > type(self).peak_len:
            type(self).peak_len = len(self)


class TestCustomerRunnerPreExtensionCapCheck:
    """BEAAA-5532 AC#1 Case C — pre-extension cap check, no overrun."""

    def test_runner_peak_buffer_bounded_by_cap_plus_one_chunk(
        self, identity_oversize_server: str
    ) -> None:
        agent_endpoint = identity_oversize_server
        runner = customer_agent_http_runner_factory(
            agent_endpoint, "fixture-token-not-real-credential"
        )

        # Inject the tracking subclass into the runner module's globals
        # so ``buf = bytearray()`` resolves to the tracker via
        # LOAD_GLOBAL. Restore after the call so other tests / runners
        # see the builtin again.
        _TrackingBytearray.reset()
        original = customer_runner_mod.__dict__.get("bytearray")
        customer_runner_mod.__dict__["bytearray"] = _TrackingBytearray
        try:
            with ScopedEgressAllowlist(_ALLOWED_BIND_HOST):
                with pytest.raises(RunnerError) as exc_info:
                    runner.complete("oversize-identity-probe-payload")
        finally:
            if original is None:
                customer_runner_mod.__dict__.pop("bytearray", None)
            else:
                customer_runner_mod.__dict__["bytearray"] = original

        # Cap error shape preserved across the iter_raw + pre-extension
        # rewrite — downstream walker error-matching is keyed on the
        # substring 'customer endpoint response exceeded'.
        assert "customer endpoint response exceeded" in str(exc_info.value), (
            f"expected cap error message preserved; got "
            f"{exc_info.value!r}"
        )

        # Peak buffer must stay within cap + ONE_CHUNK_SIZE — the
        # pre-extension check forbids the post-extension one-chunk
        # overrun the BEAAA-5528 iter_bytes path allowed. On the
        # post-fix tree the peak is at most _RUNNER_CAP_BYTES (the
        # check rejects the next chunk BEFORE extending).
        peak = _TrackingBytearray.peak_len
        ceiling = _RUNNER_CAP_BYTES + _ONE_CHUNK_SIZE
        assert peak <= ceiling, (
            f"pre-extension cap check breach: in-memory buffer reached "
            f"{peak} bytes (> {ceiling} = cap {_RUNNER_CAP_BYTES} + "
            f"one chunk {_ONE_CHUNK_SIZE}). The runner must reject the "
            f"next chunk BEFORE calling buf.extend(chunk) so the "
            f"buffer never grows past the cap by more than one chunk-"
            f"worth of slack."
        )
        # Tighter post-fix assertion: with pre-extension check the
        # peak is at most the cap itself. We leave the looser ceiling
        # above as the contract bound (so future legitimate refactors
        # within the cap-plus-one-chunk envelope don't accidentally
        # break the regression) and surface this tighter check as a
        # diagnostic if it ever drifts.
        assert peak <= _RUNNER_CAP_BYTES, (
            f"post-fix peak buffer ({peak} bytes) exceeded the cap "
            f"({_RUNNER_CAP_BYTES}). The pre-extension check should "
            f"reject the chunk that would push past the cap WITHOUT "
            f"extending the buffer — so peak == the largest "
            f"accumulated buffer that strictly fit, which by induction "
            f"is <= cap."
        )

        # Server-side acknowledgement sanity: the runner must have
        # closed the connection within a small multiple of the cap.
        # On the BEAAA-5528 streaming path the server typically gets
        # the first few chunks past the cap before the broken pipe
        # surfaces; 4x the cap is the same slack budget the BEAAA-5528
        # regression uses.
        sent = _IdentityOversizeHandler.bytes_acknowledged_before_disconnect
        wire_ceiling = 4 * _RUNNER_CAP_BYTES
        assert sent < wire_ceiling, (
            f"server flushed {sent} bytes before client disconnect, "
            f">= {wire_ceiling}. The streaming early-abort must close "
            f"the connection within a few chunks of the cap so a "
            f"misbehaving customer endpoint can't fill the pipe with "
            f"identity-encoded bytes."
        )


__all__: list[str] = []
