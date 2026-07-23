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
RUNBOOK = os.path.abspath(
    os.path.join(HERE, "..", "..", "docs", "deploy",
                 "sequence-20-production-deployment-runbook.md")
)

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


# --------------------------------------------------------------------------- #
# (5) Step-2 live-vhost mutation must be deterministic/automation-runnable — no
#     interactive $EDITOR (BEAAA-16331 operator STOP 911fcd68, item 5).
# --------------------------------------------------------------------------- #
def test_runbook_vhost_mutation_is_automation_runnable_no_editor():
    rb = _read(RUNBOOK)
    # The interactive editor must be gone from the deployable mutation. Prose may
    # still reference `$EDITOR` when explaining the fix, but no shell command line
    # may invoke it (command lines in the heredocs start at column 0).
    assert not re.search(r"(?m)^\s*\$EDITOR\b", rb), (
        "Step 2 must not shell out to $EDITOR: the live-vhost mutation has to be "
        "deterministic and automation-runnable (operator STOP 911fcd68 item 5)."
    )
    # A hash guard binds the transform to the reviewed pre-image (fail-closed).
    assert "LIVE_VHOST_SHA256" in rb, "Step 2 must hash-guard the live vhost pre-image"
    assert re.search(r'\[ "\$PRE" = "\$LIVE_VHOST_SHA256" \]', rb), \
        "the transform must refuse unless the staged copy matches the recorded hash"
    # The include is inserted exactly once and the diff is captured.
    assert "include /etc/nginx/backbond-scanner-edge.server.conf;" in rb
    assert "vhost-include.diff" in rb, "the exact unified diff must go to the receipt"
    # v1.6 identity (unique — not the spent v1.4/v1.5), item 6.
    assert re.search(r"(?m)^version:\s*1\.6\b", rb), "runbook must carry the unique v1.6 identity"


def _extract_runbook_transform() -> str:
    """Pull the exact python3 transform heredoc out of the runbook Step 2."""
    rb = _read(RUNBOOK)
    m = re.search(r"python3 - \"\$STAGE\" \"\$EDGE_UPSTREAM\" <<'PY'\n(.*?)\nPY\n",
                  rb, re.DOTALL)
    assert m, "Step 2 must embed a python3 transform heredoc"
    return m.group(1)


def test_runbook_transform_removes_scanner_locations_and_is_idempotent(tmp_path):
    """The runbook's OWN transform, run against a realistic vhost, must delete the
    scanner proxy locations, insert exactly one include, keep one server/listener,
    and be idempotent on a second pass."""
    import subprocess
    import sys

    transform = _extract_runbook_transform()
    script = tmp_path / "transform.py"
    script.write_text(transform)

    vhost = tmp_path / "scanner.backbond.ai"
    vhost.write_text(
        "server {\n"
        "    listen 443 ssl;\n"
        "    server_name scanner.backbond.ai;\n"
        "    ssl_certificate     /etc/letsencrypt/live/scanner.backbond.ai/fullchain.pem;\n"
        "    ssl_certificate_key /etc/letsencrypt/live/scanner.backbond.ai/privkey.pem;\n"
        "\n"
        "    location /scanner/v1/ {\n"
        "        proxy_pass http://127.0.0.1:8080;\n"
        "        proxy_set_header Host $host;\n"
        "    }\n"
        "    location ~ ^/scan/ {\n"
        "        proxy_pass http://127.0.0.1:8080;\n"
        "    }\n"
        "    location / {\n"
        "        proxy_pass http://127.0.0.1:8080;\n"
        "    }\n"
        "}\n"
    )

    def run():
        r = subprocess.run([sys.executable, str(script), str(vhost), "127.0.0.1:8080"],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"transform failed: {r.stderr}"

    run()
    out1 = vhost.read_text()
    include = "include /etc/nginx/backbond-scanner-edge.server.conf;"
    assert out1.count(include) == 1, "include must be inserted exactly once"
    assert len(re.findall(r"(?m)^\s*server\s*\{", out1)) == 1, "still exactly one server block"
    assert len(re.findall(r"(?m)^\s*listen\s", out1)) == 1, "no new listener introduced"
    assert not re.search(r"(?m)^\s*location\s+/scanner/", out1), "scanner proxy location removed"
    assert "proxy_pass" not in out1, "all app-tier proxy locations replaced by the include"

    run()  # idempotency: a second application must not change the file
    assert vhost.read_text() == out1, "transform must be idempotent"
