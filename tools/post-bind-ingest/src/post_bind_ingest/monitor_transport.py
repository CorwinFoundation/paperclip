"""Constrained HTTPS transport for the Passive Monitor evidence artifact.

BEAAA-23699 (P0-C). Founder accepted the G2 transport recommendation on
BEAAA-23696 (2026-08-16). This module wires the existing fail-closed monitor
artifact gate (:mod:`post_bind_ingest.egress`) to a single allowlisted HTTPS
sender with acknowledgement, bounded retry/backoff, idempotency, and immutable
delivery receipts. Default posture is no-transmit: without an explicit,
revisioned approval the artifact is prepared and validated but zero bytes leave
the box.

The sender never widens what may egress. :func:`validate_monitor_artifact`
remains the sole outbound payload gate; the only content permitted is hashes,
manifests, aggregates, approved determinations, and length-capped redacted
excerpts. Raw logs, prompts, weights, credentials, proprietary configuration,
and unredacted tool arguments cannot cross the vendor boundary.

Fail-closed is the default at every branch: absent approval flag, missing
endpoint, non-HTTPS URL, host mismatch, redirect, DNS/IP scope change, payload
mutation, invalid TLS, invalid acknowledgement, or exhausted retry all resolve
to *not delivered* with eligibility left unresolved. Absent delivery is never
treated as favorable evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import json
import socket
import time
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from .egress import (
    MAX_EXCERPT_CHARS,
    MONITOR_DATA_CONTRACT_VERSION,
    EgressGateError,
    validate_monitor_artifact,
)
from .invariants import canonical_dumps, utc_now_iso

__all__ = [
    "MonitorTransportError",
    "TransportRejected",
    "AmbiguousDeliveryError",
    "TransportConfig",
    "TransportReceiptStore",
    "TransportResult",
    "build_envelope",
    "expected_ack_hash",
    "make_https_sender",
    "send_monitor_artifact",
]


class MonitorTransportError(RuntimeError):
    """Base error for the constrained monitor transport."""


class TransportRejected(MonitorTransportError):
    """A destination or acknowledgement failed a fail-closed gate.

    Raised before any successful receipt is written. The artifact is not
    delivered and eligibility must remain unresolved.
    """


class AmbiguousDeliveryError(MonitorTransportError):
    """A send attempt neither confirmed nor cleanly failed (e.g. a timeout).

    The receiver may or may not have persisted the artifact, so the sender
    must retry with the same idempotency key rather than fabricate a receipt.
    """


def _sha256_hex(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class TransportConfig:
    """Revisioned transport configuration.

    ``config_revision`` binds this exact policy to every receipt. Transmit is
    disabled unless ``transmit_enabled`` is explicitly true, and even then the
    destination must clear the HTTPS, host, and IP allowlists below.
    """

    config_revision: str
    source_identity: str
    candidate_sha: str
    endpoint: str | None = None
    allowed_hosts: tuple[str, ...] = ()
    allowed_ips: tuple[str, ...] = ()
    schema_version: str = MONITOR_DATA_CONTRACT_VERSION
    transmit_enabled: bool = False
    allow_private_destinations: bool = False
    max_attempts: int = 3
    backoff_base_seconds: float = 0.5
    max_excerpt_chars: int = MAX_EXCERPT_CHARS

    def require_transmit_ready(self) -> tuple[str, str, int]:
        """Validate the destination, returning ``(endpoint, host, port)``.

        Fail-closed on missing endpoint, non-HTTPS scheme, missing host, or a
        host that is not on the allowlist. Raises :class:`TransportRejected`.
        """

        if not self.transmit_enabled:
            raise TransportRejected("transmit is disabled by default; explicit approval required")
        if not self.endpoint:
            raise TransportRejected("egress endpoint is required when transmit is enabled")
        parsed = urlparse(self.endpoint)
        if parsed.scheme != "https":
            raise TransportRejected("egress endpoint must use https")
        host = parsed.hostname
        if not host:
            raise TransportRejected("egress endpoint host is required")
        if not self.allowed_hosts or host not in self.allowed_hosts:
            raise TransportRejected(f"egress endpoint host {host!r} is not allowlisted")
        port = parsed.port or 443
        return self.endpoint, host, port


@dataclass
class TransportReceiptStore:
    """Append-only, immutable receipt and queue store rooted at a directory.

    Receipts and queued deliveries are keyed by idempotency key. A receipt is
    written exactly once; a second write with identical bytes is a no-op, and a
    write with different bytes for the same key is a tamper condition.
    """

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self._receipts = self.root / "receipts"
        self._queue = self.root / "queue"
        self._receipts.mkdir(parents=True, exist_ok=True)
        self._queue.mkdir(parents=True, exist_ok=True)

    def _receipt_path(self, idempotency_key: str) -> Path:
        return self._receipts / f"{idempotency_key}.json"

    def _queue_path(self, idempotency_key: str) -> Path:
        return self._queue / f"{idempotency_key}.json"

    def load_receipt(self, idempotency_key: str) -> dict[str, Any] | None:
        path = self._receipt_path(idempotency_key)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def write_receipt(self, idempotency_key: str, receipt: Mapping[str, Any]) -> dict[str, Any]:
        path = self._receipt_path(idempotency_key)
        payload = canonical_dumps(receipt)
        if path.exists():
            existing = path.read_bytes()
            if existing != payload:
                raise TransportRejected(
                    f"delivery receipt for {idempotency_key} is immutable and cannot be rewritten"
                )
            return json.loads(existing.decode("utf-8"))
        tmp = path.with_suffix(".json.tmp")
        tmp.write_bytes(payload)
        tmp.replace(path)
        # A confirmed delivery supersedes any queued copy of the same artifact.
        self._queue_path(idempotency_key).unlink(missing_ok=True)
        return dict(receipt)

    def enqueue(self, idempotency_key: str, entry: Mapping[str, Any]) -> Path:
        path = self._queue_path(idempotency_key)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_bytes(canonical_dumps(entry))
        tmp.replace(path)
        return path

    def load_queued(self, idempotency_key: str) -> dict[str, Any] | None:
        path = self._queue_path(idempotency_key)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class TransportResult:
    """Outcome of a monitor-artifact send.

    ``eligibility`` is the fail-closed disposition consumed downstream:
    ``"disabled"`` (default no-transmit), ``"delivered"`` (a hash-bound
    acknowledgement was verified), or ``"unresolved"`` (queued locally, no
    favorable determination may be drawn).
    """

    delivered: bool
    transmitted: bool
    eligibility: str
    endpoint: str | None
    artifact_hash: str
    idempotency_key: str
    bytes_prepared: int
    attempts: tuple[dict[str, Any], ...] = ()
    receipt: dict[str, Any] | None = None
    queued_path: str | None = None


def _resolve_and_check_ips(host: str, config: TransportConfig) -> tuple[str, ...]:
    """Resolve ``host`` and enforce the IP-scope policy.

    Every resolved address must be on ``allowed_ips``; a private or loopback
    address is additionally rejected unless ``allow_private_destinations`` is
    set. This closes DNS-rebinding and unapproved-destination paths.
    """

    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise TransportRejected(f"destination host {host!r} does not resolve: {exc}") from exc
    resolved = tuple(sorted({info[4][0] for info in infos}))
    if not resolved:
        raise TransportRejected(f"destination host {host!r} resolved to no addresses")
    for address in resolved:
        parsed_ip = ipaddress.ip_address(address)
        if not config.allow_private_destinations and (
            parsed_ip.is_private or parsed_ip.is_loopback or parsed_ip.is_link_local
        ):
            raise TransportRejected(
                f"destination {address} is a private/unapproved address"
            )
        if address not in config.allowed_ips:
            raise TransportRejected(
                f"destination {address} is not on the IP allowlist"
            )
    return resolved


def _idempotency_key(config: TransportConfig, artifact_hash: str, endpoint: str) -> str:
    material = canonical_dumps(
        {
            "artifact_hash": artifact_hash,
            "candidate_sha": config.candidate_sha,
            "config_revision": config.config_revision,
            "destination": endpoint,
            "source_identity": config.source_identity,
        }
    )
    return hashlib.sha256(material).hexdigest()


def build_envelope(artifact: Mapping[str, Any], *, config: TransportConfig) -> dict[str, Any]:
    """Wrap a validated artifact in the signed-by-hash outbound envelope."""

    payload = canonical_dumps(artifact)
    artifact_hash = _sha256_hex(payload)
    endpoint = config.endpoint or ""
    return {
        "schema_version": config.schema_version,
        "source_identity": config.source_identity,
        "candidate_sha": config.candidate_sha,
        "config_revision": config.config_revision,
        "destination": endpoint,
        "idempotency_key": _idempotency_key(config, artifact_hash, endpoint),
        "artifact_hash": artifact_hash,
        "artifact": artifact,
    }


def expected_ack_hash(
    *,
    idempotency_key: str,
    artifact_hash: str,
    receipt_id: str,
    schema_version: str,
    destination: str,
) -> str:
    """Recompute the acknowledgement binding the receiver must return."""

    material = canonical_dumps(
        {
            "artifact_hash": artifact_hash,
            "destination": destination,
            "idempotency_key": idempotency_key,
            "receipt_id": receipt_id,
            "schema_version": schema_version,
            "status": "accepted",
        }
    )
    return _sha256_hex(material)


def _verify_ack(ack: Mapping[str, Any], *, envelope: Mapping[str, Any]) -> str:
    """Validate a receiver acknowledgement; return the bound ack hash.

    Any deviation — wrong status, mismatched artifact hash, mismatched
    idempotency key, or a recomputed ack hash that does not match — is an
    invalid acknowledgement and fails closed.
    """

    if not isinstance(ack, Mapping):
        raise TransportRejected("acknowledgement is not an object")
    if ack.get("status") != "accepted":
        raise TransportRejected(f"acknowledgement status not accepted: {ack.get('status')!r}")
    if ack.get("artifact_hash") != envelope["artifact_hash"]:
        raise TransportRejected("acknowledgement artifact_hash does not match sent artifact")
    if ack.get("idempotency_key") != envelope["idempotency_key"]:
        raise TransportRejected("acknowledgement idempotency_key mismatch")
    receipt_id = ack.get("receipt_id")
    if not isinstance(receipt_id, str) or not receipt_id:
        raise TransportRejected("acknowledgement missing receipt_id")
    recomputed = expected_ack_hash(
        idempotency_key=envelope["idempotency_key"],
        artifact_hash=envelope["artifact_hash"],
        receipt_id=receipt_id,
        schema_version=envelope["schema_version"],
        destination=envelope["destination"],
    )
    if ack.get("ack_hash") != recomputed:
        raise TransportRejected("acknowledgement hash does not verify")
    return recomputed


# A sender transport is any callable that ships the envelope bytes over a
# validated HTTPS connection and returns the parsed acknowledgement. It must
# raise AmbiguousDeliveryError on timeouts, TransportRejected on redirects /
# invalid TLS / non-2xx, and MonitorTransportError on connection outages.
Sender = Callable[[bytes, TransportConfig, tuple[str, ...]], Mapping[str, Any]]


def send_monitor_artifact(
    artifact: Mapping[str, Any],
    *,
    config: TransportConfig,
    store: TransportReceiptStore,
    sender: Sender | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> TransportResult:
    """Prepare, gate, and (only if approved) transmit a monitor artifact.

    The artifact is always validated through :func:`validate_monitor_artifact`
    first. With transmit disabled, the function returns after preparing the
    envelope without opening any connection (zero bytes sent). With transmit
    enabled and a clean destination, it retries within ``max_attempts`` until a
    hash-bound acknowledgement verifies, writing one immutable receipt.
    Exhausted retries or an outage queue the evidence locally and leave
    eligibility unresolved.
    """

    # Sole outbound payload gate. Never bypassed, regardless of transmit state.
    validate_monitor_artifact(artifact, max_excerpt_chars=config.max_excerpt_chars)
    if config.schema_version != MONITOR_DATA_CONTRACT_VERSION:
        raise TransportRejected("transport schema_version is not the approved contract")

    envelope = build_envelope(artifact, config=config)
    envelope_bytes = canonical_dumps(envelope)
    artifact_hash = envelope["artifact_hash"]
    idempotency_key = envelope["idempotency_key"]

    # Default posture: prepared and validated, but no bytes leave the box.
    if not config.transmit_enabled:
        return TransportResult(
            delivered=False,
            transmitted=False,
            eligibility="disabled",
            endpoint=None,
            artifact_hash=artifact_hash,
            idempotency_key=idempotency_key,
            bytes_prepared=len(envelope_bytes),
        )

    endpoint, host, _port = config.require_transmit_ready()
    resolved_ips = _resolve_and_check_ips(host, config)

    # Idempotency: a prior confirmed delivery is the one logical receipt.
    existing = store.load_receipt(idempotency_key)
    if existing is not None and existing.get("delivered"):
        if existing.get("artifact_hash") != artifact_hash:
            raise TransportRejected(
                "idempotency key already bound to a different artifact hash"
            )
        return TransportResult(
            delivered=True,
            transmitted=True,
            eligibility="delivered",
            endpoint=endpoint,
            artifact_hash=artifact_hash,
            idempotency_key=idempotency_key,
            bytes_prepared=len(envelope_bytes),
            attempts=tuple(existing.get("attempt_history", ())),
            receipt=existing,
        )

    if sender is None:
        sender = make_https_sender()

    attempts: list[dict[str, Any]] = []
    transmitted = False
    for attempt in range(1, config.max_attempts + 1):
        transmitted = True
        try:
            ack = sender(envelope_bytes, config, resolved_ips)
        except AmbiguousDeliveryError as exc:
            attempts.append({"attempt": attempt, "outcome": "ambiguous", "at": utc_now_iso(), "detail": str(exc)})
            if attempt < config.max_attempts:
                sleep(config.backoff_base_seconds * (2 ** (attempt - 1)))
            continue
        except TransportRejected:
            # A hard rejection (redirect / invalid TLS / non-2xx). Fail closed
            # without a receipt; the caller must not treat this as delivered.
            attempts.append({"attempt": attempt, "outcome": "rejected", "at": utc_now_iso()})
            raise
        except MonitorTransportError as exc:
            # Outage (connection refused / DNS drop). Retry, then queue.
            attempts.append({"attempt": attempt, "outcome": "unreachable", "at": utc_now_iso(), "detail": str(exc)})
            if attempt < config.max_attempts:
                sleep(config.backoff_base_seconds * (2 ** (attempt - 1)))
            continue

        ack_hash = _verify_ack(ack, envelope=envelope)
        attempts.append({"attempt": attempt, "outcome": "acknowledged", "at": utc_now_iso()})
        receipt = {
            "receipt_id": ack["receipt_id"],
            "source_identity": config.source_identity,
            "artifact_hash": artifact_hash,
            "schema_version": config.schema_version,
            "destination": endpoint,
            "config_revision": config.config_revision,
            "candidate_sha": config.candidate_sha,
            "idempotency_key": idempotency_key,
            "attempt_history": attempts,
            "acknowledgement_hash": ack_hash,
            "delivered": True,
            "created_at": utc_now_iso(),
        }
        stored = store.write_receipt(idempotency_key, receipt)
        return TransportResult(
            delivered=True,
            transmitted=True,
            eligibility="delivered",
            endpoint=endpoint,
            artifact_hash=artifact_hash,
            idempotency_key=idempotency_key,
            bytes_prepared=len(envelope_bytes),
            attempts=tuple(attempts),
            receipt=stored,
        )

    # Retries exhausted: queue locally, leave eligibility unresolved.
    queued_entry = {
        "idempotency_key": idempotency_key,
        "artifact_hash": artifact_hash,
        "schema_version": config.schema_version,
        "destination": endpoint,
        "config_revision": config.config_revision,
        "candidate_sha": config.candidate_sha,
        "source_identity": config.source_identity,
        "envelope": envelope,
        "attempt_history": attempts,
        "status": "queued",
        "eligibility": "unresolved",
        "queued_at": utc_now_iso(),
    }
    queued_path = store.enqueue(idempotency_key, queued_entry)
    return TransportResult(
        delivered=False,
        transmitted=transmitted,
        eligibility="unresolved",
        endpoint=endpoint,
        artifact_hash=artifact_hash,
        idempotency_key=idempotency_key,
        bytes_prepared=len(envelope_bytes),
        attempts=tuple(attempts),
        queued_path=str(queued_path),
    )


def make_https_sender(*, ca_file: str | None = None, timeout: float = 10.0) -> Sender:
    """Build an HTTPS sender using stdlib TLS with certificate validation.

    ``ca_file`` pins the trust anchor (used to trust a private receiver CA);
    when omitted the system trust store is used. The returned sender treats
    redirects and non-2xx as rejections (the endpoint is pinned), surfaces
    timeouts as :class:`AmbiguousDeliveryError`, and connection failures as
    :class:`MonitorTransportError`.
    """

    import http.client
    import ssl

    def _send(
        envelope_bytes: bytes,
        config: TransportConfig,
        resolved_ips: tuple[str, ...],
    ) -> Mapping[str, Any]:
        parsed = urlparse(config.endpoint or "")
        host = parsed.hostname or ""
        port = parsed.port or 443
        context = ssl.create_default_context(cafile=ca_file)
        conn = http.client.HTTPSConnection(host, port, context=context, timeout=timeout)
        try:
            conn.request(
                "POST",
                parsed.path or "/",
                body=envelope_bytes,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(envelope_bytes)),
                },
            )
            response = conn.getresponse()
            status = response.status
            body = response.read()
        except (TimeoutError, socket.timeout) as exc:
            raise AmbiguousDeliveryError(f"timeout awaiting acknowledgement: {exc}") from exc
        except ssl.SSLError as exc:
            raise TransportRejected(f"TLS validation failed: {exc}") from exc
        except OSError as exc:
            raise MonitorTransportError(f"destination unreachable: {exc}") from exc
        finally:
            conn.close()

        if 300 <= status < 400:
            raise TransportRejected(f"redirect responses are not followed (status {status})")
        if status != 200:
            raise TransportRejected(f"receiver rejected artifact with status {status}: {body[:200]!r}")
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise TransportRejected(f"acknowledgement is not valid JSON: {exc}") from exc

    return _send
