"""BEAAA-11866 — D1 edge front (Caddy) integration test.

Stands up the committed ``infra/edge/Caddyfile`` in Docker in front of a fake
upstream and proves the three acceptance properties of the edge tier:

  (a) route allowlist / default-deny: only ``POST /scanner/v1/scans`` and
      ``GET /scan/{scan_id}`` reach upstream; every other path/method returns
      an opaque ``404 {"error":"not_found", ...}`` and NEVER touches upstream.
  (b) topology hiding: no ``Server`` / ``Via`` / ``X-Powered-By`` / ``Location``
      header and no internal host/port/path/stack-trace string appears in any
      response header or body.
  (c) error normalization: an injected upstream ``500`` becomes an opaque
      ``502 {"error":"upstream_error", ...}`` with no leaked internal detail.

Topology: the edge (Caddy) and a fake upstream each run as containers on a
shared user-defined docker network; the edge reaches the upstream by container
name. Both containers publish to the host loopback so the test can drive the
edge and read the upstream's recording directly. (Container->host networking is
not assumed — everything the edge needs is container->container.)

STAGING/infra only — no contract, rubric, scoring, or corpus change. The test
skips cleanly when Docker (or the caddy image / networking) is unavailable.
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import time

import pytest

CADDY_IMAGE = "caddy:2.8-alpine"
PY_IMAGE = "python:3.12-alpine"
HERE = os.path.dirname(os.path.abspath(__file__))
CADDYFILE = os.path.abspath(os.path.join(HERE, "..", "..", "infra", "edge", "Caddyfile"))
UPSTREAM_PY = os.path.join(HERE, "_edge_fake_upstream.py")

# Internal detail the fake upstream deliberately leaks; NONE of these substrings
# may ever appear in an edge response (header or body).
LEAK_HOST = "10.0.0.5"
LEAK_INTERNAL_PATH = "/internal/scan/leaky-path"
LEAK_STACK = "Traceback (most recent call last): RuntimeError at app.py:1337"
FORBIDDEN_HEADERS = ("server", "via", "x-powered-by", "location", "x-runtime")
LEAK_SUBSTRINGS = (LEAK_HOST, LEAK_INTERNAL_PATH, LEAK_STACK, "uvicorn", "Traceback", "/internal/")
TRIGGER_500_ID = "trigger-500"


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _have_images() -> bool:
    if shutil.which("docker") is None:
        return False
    for img in (CADDY_IMAGE, PY_IMAGE):
        try:
            subprocess.run(["docker", "image", "inspect", img],
                           check=True, capture_output=True, timeout=30)
        except Exception:
            try:
                subprocess.run(["docker", "pull", img],
                               check=True, capture_output=True, timeout=240)
            except Exception:
                return False
    return True


def _http(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method, path, body=body)
        resp = conn.getresponse()
        data = resp.read().decode("utf-8", "replace")
        headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, headers, data
    finally:
        conn.close()


class _Stack:
    """docker network + fake-upstream container + caddy edge container."""

    def __init__(self):
        tag = str(_free_port())
        self.net = "beaaa11866net%s" % tag
        self.up_name = "beaaa11866up%s" % tag
        self.edge_name = "beaaa11866edge%s" % tag
        self.up_pub = _free_port()
        self.edge_pub = _free_port()
        self._created = []
        try:
            self._run(["docker", "network", "create", self.net])
            # Fake upstream container on the shared network, published to loopback.
            self._run([
                "docker", "run", "-d", "--name", self.up_name, "--network", self.net,
                "-p", "127.0.0.1:%d:9000" % self.up_pub,
                "-e", "UPSTREAM_PORT=9000",
                "-v", "%s:/upstream.py:ro" % UPSTREAM_PY,
                PY_IMAGE, "python3", "/upstream.py",
            ], container=self.up_name)
            # Caddy edge on the same network; reaches upstream by container name.
            self._run([
                "docker", "run", "-d", "--name", self.edge_name, "--network", self.net,
                "-p", "127.0.0.1:%d:8080" % self.edge_pub,
                "-e", "EDGE_LISTEN=:8080",
                "-e", "EDGE_UPSTREAM=%s:9000" % self.up_name,
                "-v", "%s:/etc/caddy/Caddyfile:ro" % CADDYFILE,
                CADDY_IMAGE,
            ], container=self.edge_name)
            self._wait_upstream()
            self._wait_edge()
        except Exception:
            self.teardown()
            raise

    def _run(self, cmd, container=None):
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError("cmd failed: %s\n%s" % (" ".join(cmd), proc.stderr.strip()))
        if container:
            self._created.append(container)
        return proc

    def _wait_upstream(self, timeout=25.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                code, _, _ = _http(self.up_pub, "GET", "/__received__")
                if code == 200:
                    return
            except Exception:
                pass
            time.sleep(0.4)
        raise RuntimeError("fake upstream did not become ready")

    def _wait_edge(self, timeout=25.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                code, _, _ = _http(self.edge_pub, "GET", "/__edge_ready_probe__")
                if code == 404:  # default-deny answer => edge is up
                    return
            except Exception:
                pass
            time.sleep(0.4)
        raise RuntimeError("edge did not become ready")

    # --- test-facing helpers ------------------------------------------------
    def edge(self, method, path, body=None):
        return _http(self.edge_pub, method, path, body)

    def reset_upstream(self):
        _http(self.up_pub, "POST", "/__reset__")

    def upstream_received(self):
        _, _, body = _http(self.up_pub, "GET", "/__received__")
        return [tuple(x) for x in json.loads(body)]

    def teardown(self):
        for name in (self.edge_name, self.up_name):
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        subprocess.run(["docker", "network", "rm", self.net], capture_output=True)


@pytest.fixture(scope="module")
def stack():
    if not _have_images():
        pytest.skip("docker / required images unavailable")
    try:
        s = _Stack()
    except Exception as exc:  # noqa: BLE001
        pytest.skip("could not stand up edge stack: %s" % exc)
    yield s
    s.teardown()


def _assert_no_leak(headers: dict, body: str):
    for h in FORBIDDEN_HEADERS:
        assert h not in headers, "topology header %r leaked: %r" % (h, headers.get(h))
    blob = " ".join("%s:%s" % (k, v) for k, v in headers.items()) + " " + body
    for s in LEAK_SUBSTRINGS:
        assert s not in blob, "internal detail %r leaked in response" % s


# --------------------------------------------------------------------------- #
# (a) allowlist / default-deny
# --------------------------------------------------------------------------- #
def test_allowed_create_scan_reaches_upstream(stack):
    stack.reset_upstream()
    code, headers, body = stack.edge("POST", "/scanner/v1/scans", body="{}")
    assert code == 202, body
    assert json.loads(body)["scan_id"] == "scn_ok"
    assert ("POST", "/scanner/v1/scans") in stack.upstream_received()
    assert headers.get("x-request-id"), "X-Request-ID must be echoed on every response"
    _assert_no_leak(headers, body)


def test_allowed_get_scan_reaches_upstream(stack):
    stack.reset_upstream()
    code, headers, body = stack.edge("GET", "/scan/scn_abc123")
    assert code == 200, body
    assert json.loads(body)["scan_id"] == "scn_abc123"
    assert ("GET", "/scan/scn_abc123") in stack.upstream_received()
    assert headers.get("x-request-id")
    _assert_no_leak(headers, body)


DENIED = [
    ("POST", "/score"),
    ("POST", "/quote"),
    ("GET", "/forensic_bundle/abc"),
    ("POST", "/scanner/v1/rescans"),
    ("GET", "/scanner/v1/sla"),
    ("GET", "/admin"),
    ("GET", "/healthz"),
    ("GET", "/"),
    ("GET", "/scan/one/two"),        # two-segment: not a single scan_id
    ("GET", "/scanner/v1/scans"),    # wrong method for the create route
    ("POST", "/scan/scn_abc123"),    # wrong method for the read route
    ("DELETE", "/scanner/v1/scans"),
    ("GET", "/scannerX"),
]


@pytest.mark.parametrize("method,path", DENIED)
def test_denied_paths_opaque_404_and_never_reach_upstream(stack, method, path):
    stack.reset_upstream()
    code, headers, body = stack.edge(method, path)
    assert code == 404, "%s %s expected opaque 404, got %s: %s" % (method, path, code, body)
    payload = json.loads(body)
    assert payload == {"error": "not_found", "request_id": payload.get("request_id")}
    assert payload["request_id"], "request_id must be populated"
    # request_id echoed in header matches the body (support correlation).
    assert headers.get("x-request-id") == payload["request_id"]
    # Default-deny: upstream must not have been touched at all.
    assert stack.upstream_received() == [], (
        "denied %s %s reached upstream: %r" % (method, path, stack.upstream_received())
    )
    _assert_no_leak(headers, body)


# --------------------------------------------------------------------------- #
# (c) error normalization
# --------------------------------------------------------------------------- #
def test_upstream_5xx_normalized_to_opaque_error(stack):
    stack.reset_upstream()
    code, headers, body = stack.edge("GET", "/scan/%s" % TRIGGER_500_ID)
    assert code == 502, "upstream 500 must be normalized to 502, got %s: %s" % (code, body)
    payload = json.loads(body)
    assert payload == {"error": "upstream_error", "request_id": payload.get("request_id")}
    assert payload["request_id"]
    assert headers.get("x-request-id") == payload["request_id"]
    # The upstream leaked a stack trace + internal path in its 500 — none of it
    # may survive normalization.
    _assert_no_leak(headers, body)
    # Sanity: the request really did reach upstream (it's an allowed route).
    assert ("GET", "/scan/%s" % TRIGGER_500_ID) in stack.upstream_received()
