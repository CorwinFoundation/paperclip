"""Local TLS receiver for the constrained monitor evidence transport.

BEAAA-23699 (P0-C). The receiver is the vendor-boundary counterpart to
:mod:`post_bind_ingest.monitor_transport`. It accepts only the approved
``monitor-data-contract-v0.1`` schema, re-runs the outbound gate on the
received artifact, verifies the declared artifact hash (rejecting any payload
mutation in flight), deduplicates by idempotency key so a retried delivery
yields one logical receipt, and returns a hash-bound acknowledgement.

Fail-closed determination is structural: a missing or rejected artifact writes
no favorable determination. Only a fully accepted artifact records
``determination = "accepted"``; every rejection path records
``determination = "none"`` and returns a non-2xx status with no acknowledgement.

The server is intended for the automated end-to-end suite and for a real
partner activation once an exact destination and current approval are recorded.
It never emits a BackBond Score, rubric, corpus, judge, or pricing value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping

from .egress import MONITOR_DATA_CONTRACT_VERSION, EgressGateError, validate_monitor_artifact
from .invariants import canonical_dumps
from .monitor_transport import expected_ack_hash

__all__ = ["MonitorReceiver", "ReceiverLedger"]


def _sha256_hex(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


@dataclass
class ReceiverLedger:
    """In-memory record of accepted and rejected deliveries.

    ``accepted`` maps idempotency key -> stored acknowledgement so repeats are
    served identically. ``rejections`` records the reason for every refused
    delivery. No favorable determination is ever derived from a rejection.
    """

    accepted: dict[str, dict[str, Any]] = field(default_factory=dict)
    rejections: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_rejection(self, reason: str, detail: str) -> None:
        with self._lock:
            self.rejections.append({"reason": reason, "detail": detail, "determination": "none"})

    def resolve_or_store(self, idempotency_key: str, builder) -> tuple[dict[str, Any], bool]:
        """Return the stored ack for a key, or store a freshly built one.

        Returns ``(ack, is_new)``. The builder is only called once per key, so
        a retried delivery re-serves the original acknowledgement (idempotent).
        """

        with self._lock:
            existing = self.accepted.get(idempotency_key)
            if existing is not None:
                return existing, False
            ack, receipt_id = builder()
            record = dict(ack)
            record["_determination"] = "accepted"
            record["_receipt_id"] = receipt_id
            self.accepted[idempotency_key] = record
            return record, True


class _MonitorRequestHandler(BaseHTTPRequestHandler):
    server_version = "MonitorReceiver/0.1"
    protocol_version = "HTTP/1.1"

    # Injected by MonitorReceiver.
    ledger: ReceiverLedger
    destination: str
    receipt_seq: list[int]

    def log_message(self, *_args: Any) -> None:  # pragma: no cover - silence stderr
        return

    def _send_json(self, status: int, payload: Mapping[str, Any]) -> None:
        body = canonical_dumps(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _reject(self, status: int, reason: str, detail: str) -> None:
        self.server_ledger.record_rejection(reason, detail)
        self._send_json(status, {"status": "rejected", "reason": reason, "determination": "none"})

    @property
    def server_ledger(self) -> ReceiverLedger:
        return self.server.ledger  # type: ignore[attr-defined]

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._reject(400, "malformed-json", str(exc))
            return
        if not isinstance(envelope, dict):
            self._reject(400, "malformed-envelope", "envelope is not an object")
            return

        # Only the approved schema is accepted.
        if envelope.get("schema_version") != MONITOR_DATA_CONTRACT_VERSION:
            self._reject(422, "schema-version", f"unexpected {envelope.get('schema_version')!r}")
            return

        artifact = envelope.get("artifact")
        if not isinstance(artifact, dict):
            self._reject(422, "missing-artifact", "envelope has no artifact object")
            return

        # Re-run the sole outbound gate on the received bytes.
        try:
            validate_monitor_artifact(artifact)
        except EgressGateError as exc:
            self._reject(422, "gate-rejected", str(exc))
            return

        # Detect payload mutation in flight: the declared hash must match.
        recomputed = _sha256_hex(canonical_dumps(artifact))
        if envelope.get("artifact_hash") != recomputed:
            self._reject(422, "hash-mismatch", "declared artifact_hash does not match payload")
            return

        idempotency_key = envelope.get("idempotency_key")
        if not isinstance(idempotency_key, str) or not idempotency_key:
            self._reject(422, "missing-idempotency-key", "envelope has no idempotency_key")
            return

        server: MonitorReceiver = self.server  # type: ignore[assignment]

        def builder() -> tuple[dict[str, Any], str]:
            receipt_id = server.next_receipt_id()
            ack_hash = expected_ack_hash(
                idempotency_key=idempotency_key,
                artifact_hash=recomputed,
                receipt_id=receipt_id,
                schema_version=MONITOR_DATA_CONTRACT_VERSION,
                destination=server.destination,
            )
            ack = {
                "status": "accepted",
                "idempotency_key": idempotency_key,
                "artifact_hash": recomputed,
                "receipt_id": receipt_id,
                "schema_version": MONITOR_DATA_CONTRACT_VERSION,
                "destination": server.destination,
                "ack_hash": ack_hash,
            }
            return ack, receipt_id

        record, _is_new = server.ledger.resolve_or_store(idempotency_key, builder)
        ack = {k: v for k, v in record.items() if not k.startswith("_")}
        self._send_json(200, ack)


class MonitorReceiver(ThreadingHTTPServer):
    """Threaded TLS receiver enforcing the monitor data contract.

    Wraps its listening socket in a server-side TLS context. ``destination`` is
    the canonical https URL the sender pins and the ack binds. Use as a context
    manager in tests; call :meth:`base_url` for the bound endpoint.
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        *,
        certfile: str,
        keyfile: str,
        host: str = "localhost",
        bind_host: str = "127.0.0.1",
        port: int = 0,
        path: str = "/monitor",
    ) -> None:
        super().__init__((bind_host, port), _MonitorRequestHandler)
        self.ledger = ReceiverLedger()
        self._path = path
        self._advertised_host = host
        self._receipt_seq = 0
        self._seq_lock = threading.Lock()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certfile=certfile, keyfile=keyfile)
        self.socket = context.wrap_socket(self.socket, server_side=True)
        self._thread: threading.Thread | None = None

    @property
    def bound_port(self) -> int:
        return self.server_address[1]

    def base_url(self) -> str:
        return f"https://{self._advertised_host}:{self.bound_port}{self._path}"

    @property
    def destination(self) -> str:
        return self.base_url()

    def next_receipt_id(self) -> str:
        with self._seq_lock:
            self._receipt_seq += 1
            return f"rcpt-{self._receipt_seq:06d}"

    def start(self) -> "MonitorReceiver":
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._thread is None:
            return
        self.shutdown()
        self._thread.join(timeout=5)
        self._thread = None
        self.server_close()

    def __enter__(self) -> "MonitorReceiver":
        return self.start()

    def __exit__(self, *_exc: Any) -> None:
        self.stop()
