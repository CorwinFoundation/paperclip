"""BEAAA-16230 — Sequence 20 nginx edge X-Request-ID + log-correlation test.

Closes gap 1 of the Founder Stage Gate FAIL (attachment d05e00d8): the deployed
external edge is nginx and returned NO ``X-Request-ID`` on ordinary responses,
so no id existed to correlate a customer/forensic response to an access-log
line. This test stands up the committed nginx edge template
(``infra/edge/nginx/backbond-scanner-edge.conf.template``) in front of an
auth-aware fake upstream and proves:

  (1) ORDINARY 401 — ``POST /scanner/v1/scans`` with no auth carries a non-empty
      opaque ``X-Request-ID`` response header;
  (2) ORDINARY 404 — both an upstream not-found (``GET /scan/scn_missing``) and a
      default-deny path carry a non-empty ``X-Request-ID``;
  (3) EXACT log correlation — the id echoed in the response header appears
      verbatim in the edge access log (timestamp-only matching is not
      correlation);
  (4) topology hiding / error redaction preserved — no nginx version banner, no
      upstream Via/X-Powered-By/Location/X-Runtime, no internal host/path/stack,
      and an upstream 5xx normalized to an opaque ``upstream_error`` body.

Infra/config only — no contract, rubric, scoring, or corpus change. The Docker
stack test skips cleanly when Docker / the nginx image is unavailable; the
static template lint always runs.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import time

import pytest

NGINX_IMAGE = "nginx:1.27-alpine"
PY_IMAGE = "python:3.12-alpine"
HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.abspath(
    os.path.join(HERE, "..", "..", "infra", "edge", "nginx", "backbond-scanner-edge.conf.template")
)
UPSTREAM_PY = os.path.join(HERE, "_edge_fake_upstream_auth.py")

LEAK_HOST = "10.0.0.5"
LEAK_INTERNAL_PATH = "/internal/scan/leaky-path"
LEAK_STACK = "Traceback (most recent call last): RuntimeError at app.py:1337"
LEAK_SUBSTRINGS = (LEAK_HOST, LEAK_INTERNAL_PATH, LEAK_STACK, "uvicorn", "Traceback", "/internal/")
FORBIDDEN_HEADERS = ("via", "x-powered-by", "location", "x-runtime")
REQUEST_ID_RE = re.compile(r"^[0-9a-f]{16,}$")


# --------------------------------------------------------------------------- #
# (0) static lint — always runs, proves the config carries the gap-1 fix
# --------------------------------------------------------------------------- #
def test_template_declares_request_id_and_log_correlation():
    with open(TEMPLATE) as f:
        conf = f.read()
    # X-Request-ID emitted on EVERY response, incl. ordinary 4xx/5xx (`always`).
    assert re.search(r"add_header\s+X-Request-ID\s+\$request_id\s+always\s*;", conf), \
        "edge must add X-Request-ID with the `always` flag so 401/404/5xx carry it"
    # The same opaque id must be written into the access log for correlation.
    assert "log_format" in conf and "$request_id" in conf
    assert re.search(r"access_log\s+\S+\s+edge_json", conf)
    # Topology hiding preserved.
    assert "server_tokens off" in conf
    for h in ("Server", "Via", "X-Powered-By", "Location", "X-Runtime"):
        assert re.search(r"proxy_hide_header\s+%s\s*;" % h, conf), f"must hide {h}"
    # Error normalization preserved.
    assert "upstream_error" in conf and "not_found" in conf


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _have_images() -> bool:
    if shutil.which("docker") is None:
        return False
    for img in (NGINX_IMAGE, PY_IMAGE):
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


def _http(port, method, path, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read().decode("utf-8", "replace")
        hdrs = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, hdrs, data
    finally:
        conn.close()


class _Stack:
    """docker network + auth fake-upstream + nginx edge rendered from template."""

    def __init__(self):
        tag = str(_free_port())
        self.net = "beaaa16230net%s" % tag
        self.up_name = "beaaa16230up%s" % tag
        self.edge_name = "beaaa16230edge%s" % tag
        self.up_pub = _free_port()
        self.edge_pub = _free_port()
        try:
            self._run(["docker", "network", "create", self.net])
            self._run([
                "docker", "run", "-d", "--name", self.up_name, "--network", self.net,
                "-p", "127.0.0.1:%d:9000" % self.up_pub,
                "-e", "UPSTREAM_PORT=9000",
                "-v", "%s:/upstream.py:ro" % UPSTREAM_PY,
                PY_IMAGE, "python3", "/upstream.py",
            ])
            # Mount the template; the stock nginx entrypoint renders it via
            # envsubst. NGINX_ENVSUBST_FILTER=EDGE keeps nginx runtime vars
            # ($request_id, $status, ...) literal — only EDGE_* are substituted.
            self._run([
                "docker", "run", "-d", "--name", self.edge_name, "--network", self.net,
                "-p", "127.0.0.1:%d:8080" % self.edge_pub,
                "-e", "EDGE_LISTEN=8080",
                "-e", "EDGE_UPSTREAM=%s:9000" % self.up_name,
                "-e", "NGINX_ENVSUBST_FILTER=EDGE",
                "-e", "NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx/conf.d",
                "-v", "%s:/etc/nginx/templates/default.conf.template:ro" % TEMPLATE,
                NGINX_IMAGE,
            ])
            self._wait_upstream()
            self._wait_edge()
        except Exception:
            self.teardown()
            raise

    def _run(self, cmd):
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError("cmd failed: %s\n%s" % (" ".join(cmd), proc.stderr.strip()))
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
                if code == 404:
                    return
            except Exception:
                pass
            time.sleep(0.4)
        raise RuntimeError("edge did not become ready")

    def edge(self, method, path, headers=None, body=None):
        return _http(self.edge_pub, method, path, headers=headers, body=body)

    def reset_upstream(self):
        _http(self.up_pub, "POST", "/__reset__")

    def edge_logs(self) -> str:
        proc = subprocess.run(["docker", "logs", self.edge_name],
                              capture_output=True, text=True)
        return proc.stdout + proc.stderr

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
        pytest.skip("could not stand up nginx edge stack: %s" % exc)
    yield s
    s.teardown()


def _assert_no_leak(headers: dict, body: str):
    for h in FORBIDDEN_HEADERS:
        assert h not in headers, "topology header %r leaked: %r" % (h, headers.get(h))
    # nginx keeps a bare "Server: nginx" with server_tokens off; a *version* must
    # never appear (that is the fingerprint we redact).
    assert "/" not in headers.get("server", "nginx"), "nginx version leaked in Server header"
    blob = " ".join("%s:%s" % (k, v) for k, v in headers.items()) + " " + body
    for s in LEAK_SUBSTRINGS:
        assert s not in blob, "internal detail %r leaked in response" % s


# --------------------------------------------------------------------------- #
# (1) ordinary 401 carries X-Request-ID
# --------------------------------------------------------------------------- #
def test_ordinary_401_has_request_id_and_correlates(stack):
    stack.reset_upstream()
    code, headers, body = stack.edge("POST", "/scanner/v1/scans",
                                     headers={"Content-Type": "application/json"}, body="{}")
    assert code == 401, body
    rid = headers.get("x-request-id")
    assert rid and REQUEST_ID_RE.match(rid), "ordinary 401 must carry an opaque X-Request-ID"
    _assert_no_leak(headers, body)
    # exact correlation: the id must be in the edge access log verbatim.
    time.sleep(0.5)
    assert rid in stack.edge_logs(), "401 request id not found in edge access log"


# --------------------------------------------------------------------------- #
# (2) ordinary 404 — both upstream not-found and default-deny carry the id
# --------------------------------------------------------------------------- #
def test_upstream_404_has_request_id(stack):
    stack.reset_upstream()
    code, headers, body = stack.edge("GET", "/scan/scn_missing")
    assert code == 404, body
    rid = headers.get("x-request-id")
    assert rid and REQUEST_ID_RE.match(rid)
    _assert_no_leak(headers, body)
    time.sleep(0.5)
    assert rid in stack.edge_logs()


def test_default_deny_404_has_request_id_and_is_opaque(stack):
    stack.reset_upstream()
    code, headers, body = stack.edge("GET", "/scanner/v1/sla")
    assert code == 404, body
    payload = json.loads(body)
    assert payload.get("error") == "not_found"
    rid = headers.get("x-request-id")
    assert rid and payload.get("request_id") == rid, "body/header id must match for correlation"
    _assert_no_leak(headers, body)


# --------------------------------------------------------------------------- #
# (3) authed create still works and carries the id
# --------------------------------------------------------------------------- #
def test_authed_create_scan_has_request_id(stack):
    stack.reset_upstream()
    code, headers, body = stack.edge(
        "POST", "/scanner/v1/scans",
        headers={"Content-Type": "application/json", "Authorization": "Bearer test-token"},
        body="{}",
    )
    assert code == 202, body
    assert json.loads(body)["scan_id"] == "scn_ok"
    assert headers.get("x-request-id")
    _assert_no_leak(headers, body)


# --------------------------------------------------------------------------- #
# (4) upstream 5xx normalized to opaque error, still carrying the id
# --------------------------------------------------------------------------- #
def test_upstream_5xx_normalized_and_carries_request_id(stack):
    stack.reset_upstream()
    code, headers, body = stack.edge("GET", "/scan/trigger-500")
    assert code == 502, "upstream 500 must normalize to 502, got %s: %s" % (code, body)
    payload = json.loads(body)
    assert payload.get("error") == "upstream_error"
    rid = headers.get("x-request-id")
    assert rid and payload.get("request_id") == rid
    _assert_no_leak(headers, body)
