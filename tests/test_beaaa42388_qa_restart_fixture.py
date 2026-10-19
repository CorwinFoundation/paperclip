"""BEAAA-42388: credential-free restart test target + signed callback receiver.

The fixture answers the live ``openai_chat`` adapter with a fixed reply after a
bounded delay, verifies the scanner's real ``X-BackBond-Signature`` deliveries
against the dedicated ``qa-38869-callback`` ref, and keeps sanitized receipts.
The installer reruns safely and touches only that ref in the scanner env file.
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from api.ci_scan_ext import build_signed_delivery
from api.full_scan_worker import FULL_SCAN_WEBHOOK_SECRETS_ENV
from api.webhook_outbox import WEBHOOK_SECRETS_JSON_ENV

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "backbond" / "qa-restart-fixture"
HELPER = ROOT / "scripts" / "deploy" / "scanner-deploy.sh"
SECRET = "f" * 64
SITE_TEXT = """server {
    listen 443 ssl;
    server_name scanner.backbond.ai;
    location / { proxy_pass http://127.0.0.1:8077; }
    include /etc/nginx/snippets/backbond-stage-b-inbound.conf;
}
"""


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"qa_fixture_{name}", FIXTURE_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


fixture = _load("fixture")


@pytest.fixture()
def server(tmp_path):
    sleeps: list[float] = []
    srv = fixture.FixtureServer(
        ("127.0.0.1", 0),
        secret=SECRET.encode(),
        delay=fixture.bounded_delay("3"),
        receipts=fixture.ReceiptStore(tmp_path / "receipts.json"),
        sleep=sleeps.append,
    )
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    srv.sleeps = sleeps
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


def _post(srv, path: str, body: bytes, headers: dict[str, str] | None = None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{srv.server_address[1]}{path}",
        data=body,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _signed(payload: dict, secret: str = SECRET):
    return build_signed_delivery(payload, "qa-38869-callback", lambda _ref: secret)


# -- protocol / latency ------------------------------------------------------


def test_chat_speaks_openai_chat_with_auth_none_and_bounded_delay(server, capsys):
    probe = {"model": "x", "messages": [{"role": "user", "content": "PROBE-BODY-CANARY"}]}
    status, body = _post(server, fixture.CHAT_PATH, json.dumps(probe).encode())
    assert status == 200
    reply = json.loads(body)
    assert reply["object"] == "chat.completion"
    assert reply["choices"][0]["message"] == {"role": "assistant", "content": fixture.REPLY_TEXT}
    assert server.sleeps == [3.0]
    # The probe body is never echoed, stored or logged.
    assert b"PROBE-BODY-CANARY" not in body
    assert "PROBE-BODY-CANARY" not in capsys.readouterr().out
    assert not (Path(server.receipts._path)).exists()


def test_chat_rejects_non_chat_bodies(server):
    assert _post(server, fixture.CHAT_PATH, b"not json")[0] == 400
    assert _post(server, fixture.CHAT_PATH, b'{"prompt":"x"}')[0] == 400
    assert server.sleeps == []


@pytest.mark.parametrize(
    "raw,expected",
    [(None, fixture.DEFAULT_DELAY_SECONDS), ("", fixture.DEFAULT_DELAY_SECONDS), ("bad", fixture.DEFAULT_DELAY_SECONDS),
     ("nan", fixture.DEFAULT_DELAY_SECONDS), ("-4", 0.0), ("9999", fixture.MAX_DELAY_SECONDS), ("2.5", 2.5)],
)
def test_delay_is_clamped_below_the_live_runner_timeout(raw, expected):
    assert fixture.bounded_delay(raw) == expected
    assert fixture.MAX_DELAY_SECONDS < 30.0


# -- callback signatures / receipts -------------------------------------------


def test_valid_scanner_signature_is_recorded_as_sanitized_receipt(server, capsys):
    payload = {"scan_id": "scan-abc123", "status": "complete", "job_id": "j1",
               "report": {"probe": "PROBE-BODY-CANARY", "token": "bbqa_SECRETTOKEN"}}
    headers, body = _signed(payload)
    for _ in range(2):
        status, _ = _post(server, fixture.CALLBACK_PATH, body, headers)
        assert status == 204
    doc = server.receipts.snapshot()
    receipt = doc["receipts"]["scan-abc123"]
    assert receipt["terminal_status"] == "complete"
    assert receipt["delivery_count"] == 2
    assert receipt["signature_valid"] is True
    assert receipt["delivery_ids"] == [headers["X-BackBond-Delivery"]]
    persisted = (Path(server.receipts._path)).read_text()
    out = capsys.readouterr().out
    for leaked in ("PROBE-BODY-CANARY", "bbqa_SECRETTOKEN", SECRET):
        assert leaked not in persisted
        assert leaked not in out
    assert stat.S_IMODE(os.stat(server.receipts._path).st_mode) == 0o600


@pytest.mark.parametrize("variant", ["wrong_secret", "tampered", "missing", "malformed"])
def test_invalid_signature_is_rejected_and_counted_only(server, variant):
    payload = {"scan_id": "scan-bad", "status": "complete"}
    headers, body = _signed(payload, secret="e" * 64 if variant == "wrong_secret" else SECRET)
    if variant == "tampered":
        body = body.replace(b"complete", b"failed__")
    if variant == "missing":
        headers.pop("X-BackBond-Signature")
    if variant == "malformed":
        headers["X-BackBond-Signature"] = "md5=abc"
    status, _ = _post(server, fixture.CALLBACK_PATH, body, headers)
    assert status == 401
    doc = server.receipts.snapshot()
    assert doc["receipts"] == {}
    assert doc["invalid_signature_deliveries"] == 1


def test_untrusted_fields_are_not_copied_into_receipts(server):
    headers, body = _signed({"scan_id": "../../etc/passwd", "status": "complete"})
    assert _post(server, fixture.CALLBACK_PATH, body, headers)[0] == 422
    headers, body = _signed({"scan_id": "scan-ok", "status": "<script>"})
    assert _post(server, fixture.CALLBACK_PATH, body, headers)[0] == 204
    assert server.receipts.snapshot()["receipts"]["scan-ok"]["terminal_status"] == "unrecognized"


def test_there_is_no_public_receipt_or_body_endpoint(server):
    port = server.server_address[1]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{fixture.HEALTH_PATH}", timeout=5) as resp:
        assert json.loads(resp.read()) == {"ok": True, "service": "backbond-qa-restart-fixture"}
    for path in ("/qa-restart-fixture/v1/receipts", "/qa-restart-fixture/receipts.json", "/"):
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5)
        assert err.value.code == 404


def test_receipts_survive_a_fixture_restart(tmp_path):
    store = fixture.ReceiptStore(tmp_path / "receipts.json")
    store.record_valid({"scan_id": "scan-r", "status": "complete"}, None)
    again = fixture.ReceiptStore(tmp_path / "receipts.json")
    assert again.snapshot()["receipts"]["scan-r"]["delivery_count"] == 1


# -- installer ----------------------------------------------------------------


def _serve(srv):
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


@pytest.fixture(scope="module")
def healthz_url():
    """A real fixture's health endpoint, standing in for the installed unit."""
    srv = fixture.FixtureServer(("127.0.0.1", 0), secret=SECRET.encode(), delay=0.0,
                                receipts=fixture.ReceiptStore(Path(os.devnull)), sleep=lambda _s: None)
    yield _serve(srv) + fixture.HEALTH_PATH
    srv.shutdown()
    srv.server_close()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _install_env(tmp_path: Path, health_url: str) -> dict[str, str]:
    (tmp_path / "sites").mkdir()
    (tmp_path / "sites" / "scanner.backbond.ai").write_text(SITE_TEXT)
    calls = tmp_path / "calls.log"
    fake = tmp_path / "fake-cmd"
    # systemctl stand-in: logs calls, reports the unit inactive, succeeds otherwise.
    fake.write_text(f'#!/bin/sh\necho "$0 $*" >> {calls}\n[ "$1" = is-active ] && exit 3\nexit 0\n')
    fake.chmod(0o755)
    env_file = tmp_path / "scanner-edge.env"
    env_file.write_text(
        "SCANNER_EDGE_IMAGE=ghcr.io/x/y@sha256:" + "a" * 64 + "\n"
        + WEBHOOK_SECRETS_JSON_ENV + '={"vendor-a":"keep-a","vendor-b":"keep-b"}\n'
    )
    env_file.chmod(0o600)
    return {
        **os.environ,
        "QA_FIXTURE_DIR": str(tmp_path / "opt"),
        "QA_FIXTURE_SECRET_FILE": str(tmp_path / "etc" / "qa-38869-callback.secret"),
        "QA_FIXTURE_UNIT_FILE": str(tmp_path / "unit.service"),
        "QA_FIXTURE_NGINX_SNIPPET": str(tmp_path / "snippet.conf"),
        "QA_FIXTURE_NGINX_SITE": str(tmp_path / "sites" / "scanner.backbond.ai"),
        "QA_FIXTURE_RECEIPTS": str(tmp_path / "receipts.json"),
        "QA_FIXTURE_HEALTH_URL": health_url,
        "QA_FIXTURE_HEALTH_WAIT_SECONDS": "1",
        "QA_FIXTURE_PORT": str(_free_port()),
        "SCANNER_EDGE_ENV": str(env_file),
        "SCANNER_SYSTEMCTL": str(fake),
        "QA_FIXTURE_NGINX": str(fake),
    }


def _run(env, *args):
    return subprocess.run([sys.executable, str(FIXTURE_DIR / "install.py"), *args],
                          env=env, capture_output=True, text=True, timeout=60)


def _refs(env) -> dict[str, dict[str, str]]:
    out = {}
    for line in Path(env["SCANNER_EDGE_ENV"]).read_text().splitlines():
        key, _, value = line.partition("=")
        if key in (WEBHOOK_SECRETS_JSON_ENV, FULL_SCAN_WEBHOOK_SECRETS_ENV):
            out[key] = json.loads(value)
    return out


def test_install_reruns_safely_and_preserves_other_refs(tmp_path, healthz_url):
    env = _install_env(tmp_path, healthz_url)
    first = _run(env, "install", "--delay-seconds", "4")
    assert first.returncode == 0, first.stderr
    secret = Path(env["QA_FIXTURE_SECRET_FILE"]).read_text().strip()
    assert stat.S_IMODE(os.stat(env["QA_FIXTURE_SECRET_FILE"]).st_mode) == 0o600
    refs = _refs(env)
    assert refs[WEBHOOK_SECRETS_JSON_ENV] == {"vendor-a": "keep-a", "vendor-b": "keep-b", "qa-38869-callback": secret}
    assert refs[FULL_SCAN_WEBHOOK_SECRETS_ENV] == {"qa-38869-callback": secret}
    site = Path(env["QA_FIXTURE_NGINX_SITE"]).read_text()
    assert site.count("backbond-qa-restart-fixture") == 0  # include points at the snippet path
    assert site.count(env["QA_FIXTURE_NGINX_SNIPPET"]) == 1
    assert "FIXTURE_DELAY_SECONDS=4" in Path(env["QA_FIXTURE_UNIT_FILE"]).read_text()
    assert (Path(env["QA_FIXTURE_DIR"]) / "fixture.py").read_bytes() == (FIXTURE_DIR / "fixture.py").read_bytes()

    env_before = Path(env["SCANNER_EDGE_ENV"]).read_bytes()
    site_before = site
    second = _run(env, "install", "--delay-seconds", "4")
    assert second.returncode == 0, second.stderr
    assert Path(env["QA_FIXTURE_SECRET_FILE"]).read_text().strip() == secret
    assert Path(env["SCANNER_EDGE_ENV"]).read_bytes() == env_before
    assert Path(env["QA_FIXTURE_NGINX_SITE"]).read_text() == site_before
    assert "reused" in second.stdout and "unchanged" in second.stdout

    for result in (first, second):
        assert secret not in result.stdout + result.stderr
        assert "keep-a" not in result.stdout + result.stderr


def test_remove_drops_only_the_fixture_and_its_ref(tmp_path, healthz_url):
    env = _install_env(tmp_path, healthz_url)
    assert _run(env, "install").returncode == 0
    Path(env["QA_FIXTURE_RECEIPTS"]).write_text('{"receipts":{"scan-x":{"scan_id":"scan-x"}}}')
    removed = _run(env, "remove")
    assert removed.returncode == 0, removed.stderr
    assert _refs(env) == {WEBHOOK_SECRETS_JSON_ENV: {"vendor-a": "keep-a", "vendor-b": "keep-b"}}
    assert Path(env["QA_FIXTURE_NGINX_SITE"]).read_text() == SITE_TEXT
    assert not Path(env["QA_FIXTURE_SECRET_FILE"]).exists()
    assert not Path(env["QA_FIXTURE_UNIT_FILE"]).exists()
    assert not Path(env["QA_FIXTURE_NGINX_SNIPPET"]).exists()
    assert Path(env["QA_FIXTURE_RECEIPTS"]).exists()
    assert _run(env, "remove").returncode == 0


def test_install_refuses_to_rewrite_a_malformed_ref_map(tmp_path, healthz_url):
    env = _install_env(tmp_path, healthz_url)
    Path(env["SCANNER_EDGE_ENV"]).write_text(WEBHOOK_SECRETS_JSON_ENV + "=not-json\n")
    result = _run(env, "install")
    assert result.returncode != 0
    assert Path(env["SCANNER_EDGE_ENV"]).read_text() == WEBHOOK_SECRETS_JSON_ENV + "=not-json\n"


def test_receipts_and_status_print_sanitized_fields_only(tmp_path, healthz_url):
    env = _install_env(tmp_path, healthz_url)
    assert _run(env, "install").returncode == 0
    Path(env["QA_FIXTURE_RECEIPTS"]).write_text(json.dumps({
        "invalid_signature_deliveries": 1,
        "receipts": {"scan-y": {"scan_id": "scan-y", "terminal_status": "complete", "delivery_count": 1,
                                 "signature_valid": True, "body": "PROBE-BODY-CANARY"}},
    }))
    out = _run(env, "receipts")
    assert out.returncode == 0, out.stderr
    doc = json.loads(out.stdout)
    assert doc["receipts"]["scan-y"] == {"scan_id": "scan-y", "terminal_status": "complete",
                                         "delivery_count": 1, "signature_valid": True}
    assert "PROBE-BODY-CANARY" not in out.stdout
    status = _run(env, "status")
    assert status.returncode == 0, status.stderr
    assert "receipts=1 invalid_signature_deliveries=1" in status.stdout


def _assert_nothing_published(env) -> None:
    assert Path(env["QA_FIXTURE_NGINX_SITE"]).read_text() == SITE_TEXT
    assert not Path(env["QA_FIXTURE_NGINX_SNIPPET"]).exists()
    assert _refs(env) == {WEBHOOK_SECRETS_JSON_ENV: {"vendor-a": "keep-a", "vendor-b": "keep-b"}}


def test_install_refuses_a_port_held_by_another_listener(tmp_path, healthz_url):
    env = _install_env(tmp_path, healthz_url)
    with socket.socket() as other:
        other.bind(("127.0.0.1", 0))
        other.listen()
        env["QA_FIXTURE_PORT"] = str(other.getsockname()[1])
        result = _run(env, "install")
    assert result.returncode != 0
    assert "already used by another listener" in result.stderr
    _assert_nothing_published(env)
    assert not Path(env["QA_FIXTURE_SECRET_FILE"]).exists()
    assert not Path(env["QA_FIXTURE_UNIT_FILE"]).exists()


class _ForeignApp(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.mark.parametrize("target", ["foreign", "down"])
def test_install_publishes_nothing_unless_the_fixture_itself_answers(tmp_path, target):
    foreign = HTTPServer(("127.0.0.1", 0), _ForeignApp)
    url = _serve(foreign) + fixture.HEALTH_PATH
    try:
        env = _install_env(tmp_path, url if target == "foreign" else "http://127.0.0.1:9/never")
        result = _run(env, "install")
    finally:
        foreign.shutdown()
        foreign.server_close()
    assert result.returncode != 0
    assert f"fixture health={target}" in result.stderr
    _assert_nothing_published(env)
    assert "disable --now backbond-qa-restart-fixture.service" in (tmp_path / "calls.log").read_text()


def test_deploy_helper_extracts_fixture_from_live_image():
    helper = HELPER.read_text()
    assert 'qa_restart_fixture "$@"' in helper
    assert '"$CONTAINER:/app/backbond/qa-restart-fixture/."' in helper
    assert subprocess.run(["bash", "-n", str(HELPER)]).returncode == 0
    usage = subprocess.run(["bash", str(HELPER), "qa-restart-fixture", "bogus"], capture_output=True, text=True)
    assert usage.returncode != 0
    assert "usage: scanner-deploy qa-restart-fixture" in usage.stderr
