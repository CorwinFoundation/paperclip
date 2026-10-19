#!/usr/bin/env python3
"""BEAAA-42388: credential-free restart test target + signed callback receiver.

One stdlib-only process, bound to loopback and published by nginx under
``https://scanner.backbond.ai/qa-restart-fixture/``:

* ``POST /qa-restart-fixture/v1/chat/completions`` answers in the
  ``openai_chat`` live adapter shape with ``auth: none``. Every reply is the
  same fixed refusal after a bounded delay, so a real scan stays in flight
  long enough to cross a deploy without touching production timeouts. No
  model is called and no request body is stored or logged.
* ``POST /qa-restart-fixture/v1/callback`` verifies ``X-BackBond-Signature``
  (HMAC-SHA256 over the delivered bytes) against the dedicated
  ``qa-38869-callback`` secret and keeps a sanitized receipt per scan_id:
  terminal status, timestamps, delivery count and signature validity. The
  callback body itself is never stored.

There is no public read endpoint: receipts live in the systemd state
directory and are read by ``scanner-deploy qa-restart-fixture receipts``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PREFIX = "/qa-restart-fixture"
CHAT_PATH = f"{PREFIX}/v1/chat/completions"
CALLBACK_PATH = f"{PREFIX}/v1/callback"
HEALTH_PATH = f"{PREFIX}/healthz"

SIGNATURE_HEADER = "X-BackBond-Signature"
DELIVERY_HEADER = "X-BackBond-Delivery"
SIGNATURE_PREFIX = "sha256="

#: Per-request delay ceiling. The live customer runner times out at 30 s per
#: request, so the fixture never approaches it.
MAX_DELAY_SECONDS = 25.0
DEFAULT_DELAY_SECONDS = 8.0
MAX_CHAT_BODY_BYTES = 256 * 1024
MAX_CALLBACK_BODY_BYTES = 1024 * 1024
MAX_IN_FLIGHT = 16
MAX_RECEIPTS = 200

REPLY_TEXT = "I can't help with that request."
_SCAN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,63}")
_DELIVERY_ID_RE = re.compile(r"[0-9a-fA-F-]{8,64}")
_STATUS_RE = re.compile(r"[a-z_]{1,32}")


def bounded_delay(raw: str | None) -> float:
    """Parse the configured delay and clamp it to ``[0, MAX_DELAY_SECONDS]``."""
    try:
        value = float(raw) if raw not in (None, "") else DEFAULT_DELAY_SECONDS
    except ValueError:
        value = DEFAULT_DELAY_SECONDS
    if value != value:  # NaN
        value = DEFAULT_DELAY_SECONDS
    return min(max(value, 0.0), MAX_DELAY_SECONDS)


def chat_completion() -> dict[str, Any]:
    """The fixed OpenAI-compatible completion every probe receives."""
    return {
        "id": "qa-restart-fixture",
        "object": "chat.completion",
        "model": "backbond-qa-restart-fixture",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": REPLY_TEXT},
                "finish_reason": "stop",
            }
        ],
    }


def signature_valid(secret: bytes, body: bytes, header_value: str | None) -> bool:
    """Constant-time check of ``sha256=<hex>`` over the exact delivered bytes."""
    if not secret or not isinstance(header_value, str):
        return False
    if not header_value.startswith(SIGNATURE_PREFIX):
        return False
    expected = SIGNATURE_PREFIX + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), header_value.encode())


def _clean(value: Any, pattern: re.Pattern[str]) -> str | None:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


class ReceiptStore:
    """Sanitized, size-capped receipts persisted atomically as one JSON file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = {}
        receipts = data.get("receipts") if isinstance(data, dict) else None
        self._receipts: dict[str, dict[str, Any]] = (
            {k: v for k, v in receipts.items() if isinstance(v, dict)}
            if isinstance(receipts, dict) else {}
        )
        invalid = data.get("invalid_signature_deliveries") if isinstance(data, dict) else 0
        self._invalid = invalid if isinstance(invalid, int) else 0

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._document()

    def _document(self) -> dict[str, Any]:
        return {
            "schema_version": "backbond-qa-restart-callback-receipts@1",
            "secret_ref": "qa-38869-callback",
            "invalid_signature_deliveries": self._invalid,
            "receipts": self._receipts,
        }

    def record_invalid(self) -> None:
        with self._lock:
            self._invalid += 1
            self._persist()

    def record_valid(self, payload: Any, delivery_id: str | None) -> str | None:
        if not isinstance(payload, dict):
            return None
        scan_id = _clean(payload.get("scan_id"), _SCAN_ID_RE)
        if scan_id is None:
            return None
        status = _clean(payload.get("status"), _STATUS_RE) or "unrecognized"
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._lock:
            receipt = self._receipts.get(scan_id)
            if receipt is None:
                if len(self._receipts) >= MAX_RECEIPTS:
                    oldest = min(self._receipts, key=lambda k: self._receipts[k].get("first_received_at", ""))
                    del self._receipts[oldest]
                receipt = {"scan_id": scan_id, "first_received_at": now, "delivery_count": 0, "delivery_ids": []}
                self._receipts[scan_id] = receipt
            receipt["delivery_count"] = int(receipt.get("delivery_count", 0)) + 1
            receipt["last_received_at"] = now
            receipt["terminal_status"] = status
            receipt["signature_valid"] = True
            ids = receipt.setdefault("delivery_ids", [])
            if delivery_id and delivery_id not in ids and len(ids) < 20:
                ids.append(delivery_id)
            self._persist()
        return scan_id

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".receipts.", dir=str(self._path.parent))
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(self._document(), fh, sort_keys=True, separators=(",", ":"))
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], *, secret: bytes, delay: float, receipts: ReceiptStore,
                 sleep=time.sleep) -> None:
        super().__init__(address, FixtureHandler)
        self.secret = secret
        self.delay = delay
        self.receipts = receipts
        self.sleep = sleep
        self.slots = threading.BoundedSemaphore(MAX_IN_FLIGHT)


class FixtureHandler(BaseHTTPRequestHandler):
    server: FixtureServer
    server_version = "backbond-qa-restart-fixture"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Request lines carry no bodies, but keep stdout to method/path/status.
        return

    def _reply(self, code: int, payload: dict[str, Any] | None = None) -> None:
        body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(code)
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _read_body(self, limit: int) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return None
        if length < 0 or length > limit:
            return None
        return self.rfile.read(length)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == HEALTH_PATH:
            self._reply(200, {"ok": True})
        else:
            self._reply(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == CHAT_PATH:
            self._chat()
        elif self.path == CALLBACK_PATH:
            self._callback()
        else:
            self._reply(404, {"error": "not_found"})

    def _chat(self) -> None:
        if not self.server.slots.acquire(blocking=False):
            self._reply(503, {"error": "busy"})
            return
        try:
            body = self._read_body(MAX_CHAT_BODY_BYTES)
            if body is None:
                self._reply(413, {"error": "body_too_large"})
                return
            try:
                request = json.loads(body)
            except ValueError:
                request = None
            if not isinstance(request, dict) or not isinstance(request.get("messages"), list):
                self._reply(400, {"error": "messages_required"})
                return
            self.server.sleep(self.server.delay)
            self._reply(200, chat_completion())
        finally:
            self.server.slots.release()

    def _callback(self) -> None:
        body = self._read_body(MAX_CALLBACK_BODY_BYTES)
        if body is None:
            self._reply(413, {"error": "body_too_large"})
            return
        if not signature_valid(self.server.secret, body, self.headers.get(SIGNATURE_HEADER)):
            self.server.receipts.record_invalid()
            print("callback signature_valid=false", flush=True)
            self._reply(401, {"error": "signature_invalid"})
            return
        try:
            payload = json.loads(body)
        except ValueError:
            payload = None
        delivery_id = _clean(self.headers.get(DELIVERY_HEADER), _DELIVERY_ID_RE)
        scan_id = self.server.receipts.record_valid(payload, delivery_id)
        if scan_id is None:
            self._reply(422, {"error": "scan_id_required"})
            return
        print(f"callback signature_valid=true scan_id={scan_id}", flush=True)
        self._reply(204)


def load_secret(path: str) -> bytes:
    secret = Path(path).read_bytes().strip()
    if len(secret) < 32:
        raise SystemExit("qa-38869-callback secret is missing or too short")
    return secret


def main(argv: list[str] | None = None) -> int:
    del argv
    credentials = os.environ.get("CREDENTIALS_DIRECTORY", "")
    secret_file = os.environ.get("FIXTURE_SECRET_FILE") or os.path.join(credentials, "callback-secret")
    state_dir = os.environ.get("FIXTURE_STATE_DIR") or os.environ.get("STATE_DIRECTORY") or "."
    server = FixtureServer(
        (os.environ.get("FIXTURE_HOST", "127.0.0.1"), int(os.environ.get("FIXTURE_PORT", "8791"))),
        secret=load_secret(secret_file),
        delay=bounded_delay(os.environ.get("FIXTURE_DELAY_SECONDS")),
        receipts=ReceiptStore(Path(state_dir) / "receipts.json"),
    )
    print(f"qa-restart-fixture listening port={server.server_address[1]} delay={server.delay}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
