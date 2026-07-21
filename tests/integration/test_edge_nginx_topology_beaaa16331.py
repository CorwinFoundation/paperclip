"""BEAAA-16331 — Sequence 20 edge topology-correctness (static, always runs).

Reproduces the topology DEFECT that halted the Sequence 20 operator run
(BEAAA-16232 stop 962f9417) and then validates the correction.

THE DEFECT
   Production `scanner.backbond.ai` is served by exactly one TLS vhost at
   /etc/nginx/sites-available/scanner.backbond.ai (symlinked from
   sites-enabled/). The prior deployable surface,
   `infra/edge/nginx/backbond-scanner-edge.conf.template`, was a COMPLETE
   `server {}` block with its own `listen ${EDGE_LISTEN}` and `server_name _`.
   Installed live it would have created a SECOND, parallel/conflicting TLS
   listener alongside the real vhost.

THE CORRECTION
   The deployable surface is split into two topology-correct fragments that add
   NO server block and NO listener:
     - backbond-scanner-edge.http.conf            (HTTP scope: log_format only)
     - backbond-scanner-edge.server.conf.template (SERVER scope: include pulled
                                                   INTO the existing vhost)

This module is pure static analysis: no Docker, no network. It fails first
against the defective shape and passes only against the corrected two-fragment
surface. Infra/config only — no contract, rubric, scoring, or corpus change.
"""
from __future__ import annotations

import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
NGINX_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "infra", "edge", "nginx"))
HTTP_FRAGMENT = os.path.join(NGINX_DIR, "backbond-scanner-edge.http.conf")
SERVER_FRAGMENT = os.path.join(NGINX_DIR, "backbond-scanner-edge.server.conf.template")
LEGACY_STANDALONE = os.path.join(NGINX_DIR, "backbond-scanner-edge.conf.template")

# nginx directives that only ever appear at (or open) server/http scope and would
# therefore create or duplicate a listener/vhost if present in the deployable
# fragments. These are exactly what the operator STOP was about.
LISTENER_DIRECTIVES = (
    re.compile(r"(^|\n)\s*server\s*\{"),          # opens a server block
    re.compile(r"(^|\n)\s*listen\s"),             # binds a socket
    re.compile(r"(^|\n)\s*server_name\s"),        # declares a vhost name
    re.compile(r"(^|\n)\s*ssl_certificate\s"),    # terminates TLS
    re.compile(r"(^|\n)\s*ssl_certificate_key\s"),
    re.compile(r"\$\{EDGE_LISTEN\}"),             # the removed listen placeholder
)


def _read(path: str) -> str:
    with open(path) as f:
        return f.read()


# --------------------------------------------------------------------------- #
# (0) the corrected fragments must both exist
# --------------------------------------------------------------------------- #
def test_corrected_fragments_exist():
    assert os.path.isfile(HTTP_FRAGMENT), "HTTP-scope log_format fragment missing"
    assert os.path.isfile(SERVER_FRAGMENT), "server-scope include fragment missing"


# --------------------------------------------------------------------------- #
# (1) DEFECT reproduction: the deployable server fragment must NOT be able to
#     stand up a listener/vhost. This assertion FAILS against the old standalone
#     `server {}` template and PASSES against the include fragment.
# --------------------------------------------------------------------------- #
def test_server_fragment_declares_no_listener_or_vhost():
    conf = _read(SERVER_FRAGMENT)
    # strip comments so a directive mentioned in prose can't trip the scan.
    body = "\n".join(l for l in conf.splitlines() if not l.lstrip().startswith("#"))
    for rx in LISTENER_DIRECTIVES:
        assert not rx.search(body), (
            "deployable server include must not carry %r — that is the parallel-"
            "listener topology defect (BEAAA-16232 stop)" % rx.pattern
        )
    # it is an include fragment: it must NOT wrap its directives in `server {`,
    # but it MUST carry the location allowlist that goes inside the live vhost.
    assert "location = /scanner/v1/scans" in body
    assert "@upstream_error" in body


def test_http_fragment_is_log_format_only_no_server():
    conf = _read(HTTP_FRAGMENT)
    body = "\n".join(l for l in conf.splitlines() if not l.lstrip().startswith("#"))
    assert re.search(r"log_format\s+edge_json", body), "http fragment must define edge_json"
    # http-scope fragment must open no server block and bind no socket.
    assert not re.search(r"(^|\n)\s*server\s*\{", body)
    assert not re.search(r"(^|\n)\s*listen\s", body)
    # log_format is HTTP-scope; it must live here, not in the server include.
    server_body = "\n".join(
        l for l in _read(SERVER_FRAGMENT).splitlines() if not l.lstrip().startswith("#")
    )
    assert "log_format" not in server_body, \
        "log_format is an http-context directive; keep it out of the server include"


# --------------------------------------------------------------------------- #
# (2) the legacy standalone `server {}` template must be RETIRED so it can never
#     be installed as a parallel listener again.
# --------------------------------------------------------------------------- #
def test_legacy_standalone_server_template_is_removed():
    assert not os.path.exists(LEGACY_STANDALONE), (
        "the standalone server{} template must be deleted; its listen/server_name "
        "block is the topology defect. Deployable surface is now the two fragments."
    )


# --------------------------------------------------------------------------- #
# (3) CORRECTION: across the two fragments the full reviewed posture survives.
# --------------------------------------------------------------------------- #
def test_two_fragments_preserve_full_edge_posture():
    http = _read(HTTP_FRAGMENT)
    server = _read(SERVER_FRAGMENT)
    combined = http + "\n" + server

    # X-Request-ID emitted on EVERY response, incl. ordinary 4xx/5xx (`always`).
    assert re.search(r"add_header\s+X-Request-ID\s+\$request_id\s+always\s*;", server), \
        "edge must add X-Request-ID with the `always` flag so 401/404/5xx carry it"
    # same opaque id written into the access log (correlation) and forwarded up.
    assert re.search(r"log_format\s+edge_json", http)
    assert re.search(r"access_log\s+\S+\s+edge_json", server)
    assert re.search(r"proxy_set_header\s+X-Request-ID\s+\$request_id\s*;", server)
    # topology hiding preserved.
    assert "server_tokens off" in server
    for h in ("Server", "Via", "X-Powered-By", "Location", "X-Runtime", "X-AspNet-Version"):
        assert re.search(r"proxy_hide_header\s+%s\s*;" % h, server), f"must hide {h}"
    # allowlist / default-deny / error normalization preserved.
    assert "location = /scanner/v1/scans" in server
    assert re.search(r'location\s+~\s+"\^/scan/\[\^/\]\+\$"', server)
    assert re.search(r"location\s+/\s*\{", server), "default-deny catch-all location must exist"
    assert "upstream_error" in server and "not_found" in server
    # only EDGE_UPSTREAM is templated now (EDGE_LISTEN is gone with the listener).
    assert "${EDGE_UPSTREAM}" in server
    assert "${EDGE_LISTEN}" not in combined


# --------------------------------------------------------------------------- #
# (4) the two fragments compose into a config nginx accepts INSIDE a vhost — and
#     reject when the server fragment is (mis)used as a standalone top-level
#     config, i.e. outside a server block. Requires the `nginx` binary; skips
#     cleanly when it is unavailable (e.g. CI without nginx).
# --------------------------------------------------------------------------- #
def _have_nginx() -> bool:
    import shutil
    return shutil.which("nginx") is not None


@pytest.mark.skipif(not _have_nginx(), reason="nginx binary unavailable")
def test_fragments_syntax_check_inside_a_synthesized_vhost(tmp_path):
    import subprocess

    server_rendered = _read(SERVER_FRAGMENT).replace("${EDGE_UPSTREAM}", "127.0.0.1:8077")
    # rewrite the access_log to a writable path for the check.
    log_path = tmp_path / "edge.access.log"
    server_rendered = re.sub(
        r"access_log\s+\S+\s+edge_json;",
        f"access_log {log_path} edge_json;",
        server_rendered,
    )
    (tmp_path / "server-include.conf").write_text(server_rendered)
    (tmp_path / "http.conf").write_text(_read(HTTP_FRAGMENT))

    # A minimal but complete nginx.conf that embeds the fragments the way the
    # runbook does: http-scope log_format + a server block that INCLUDES the
    # server fragment. This proves the include is valid inside a vhost.
    good = tmp_path / "nginx.good.conf"
    good.write_text(
        "events {}\n"
        "http {\n"
        f"  include {tmp_path/'http.conf'};\n"
        "  server {\n"
        "    listen 8080;\n"
        "    server_name _;\n"
        f"    include {tmp_path/'server-include.conf'};\n"
        "  }\n"
        "}\n"
    )
    r = subprocess.run(
        ["nginx", "-t", "-c", str(good), "-p", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"fragments must validate inside a vhost:\n{r.stderr}"

    # And the server fragment used at TOP LEVEL (no server block) must be
    # REJECTED — proving it cannot masquerade as a standalone listener config.
    bad = tmp_path / "nginx.bad.conf"
    bad.write_text(
        "events {}\n"
        "http {\n"
        f"  include {tmp_path/'http.conf'};\n"
        f"  include {tmp_path/'server-include.conf'};\n"  # server directives at http scope
        "}\n"
    )
    r2 = subprocess.run(
        ["nginx", "-t", "-c", str(bad), "-p", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert r2.returncode != 0, (
        "server fragment must NOT be valid at http/top scope; if it is, it could "
        "be installed standalone and reintroduce the listener defect"
    )
