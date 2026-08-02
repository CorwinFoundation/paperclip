from __future__ import annotations

import hashlib
import base64
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from cryptography.hazmat.primitives.asymmetric import ed25519

from .invariants import canonical_dumps
from .receiver.schema import compute_batch_sha256


class OTelDriverError(ValueError):
    """Fail-closed rejection at the vendor-local OTLP boundary."""


SOURCE_DRIVER = "otel-collector-direct-v1"
EVIDENCE_TIER = "vendor-local-redacted"
SCHEMA_VERSION = "1.0"

# Deliberately smaller than the OTel semantic-convention surface. Expanding this
# set is a reviewed implementation change because every admitted value is signed.
RESOURCE_ALLOWLIST = frozenset({"service.name", "service.version", "deployment.environment.name"})
SPAN_ALLOWLIST = frozenset(
    {
        "gen_ai.operation.name",
        "gen_ai.provider.name",
        "gen_ai.request.model",
        "gen_ai.response.model",
        "gen_ai.response.id",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "error.type",
    }
)
CONTENT_ATTRIBUTES = frozenset(
    {
        "gen_ai.prompt",
        "gen_ai.completion",
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "gen_ai.system_instructions",
        "gen_ai.tool.call.arguments",
        "gen_ai.tool.call.result",
    }
)
SECRET_RE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?:api[_-]?key|authorization|password|secret|token)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)


def _iso_from_unix_nano(value: str) -> str:
    try:
        nanos = int(value)
    except (TypeError, ValueError) as exc:
        raise OTelDriverError("span time must be a unix-nanosecond integer") from exc
    if nanos <= 0:
        raise OTelDriverError("span time must be positive")
    return datetime.fromtimestamp(nanos / 1_000_000_000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _attrs(items: Any, *, path: str) -> dict[str, Any]:
    if items is None:
        return {}
    if not isinstance(items, list):
        raise OTelDriverError(f"{path} must be a list")
    result: dict[str, Any] = {}
    for item in items:
        if not isinstance(item, Mapping) or not isinstance(item.get("key"), str):
            raise OTelDriverError(f"{path} contains an invalid attribute")
        key = item["key"]
        if key in result:
            raise OTelDriverError(f"duplicate attribute rejected: {key}")
        raw = item.get("value")
        if not isinstance(raw, Mapping) or len(raw) != 1:
            raise OTelDriverError(f"attribute {key} must have one typed value")
        value = next(iter(raw.values()))
        if not isinstance(value, (str, int, float, bool)):
            raise OTelDriverError(f"attribute {key} has unsupported value type")
        result[key] = value
    return result


def _approved(attrs: Mapping[str, Any], allowed: frozenset[str], *, content_enabled: bool) -> dict[str, Any]:
    unknown = set(attrs) - allowed - CONTENT_ATTRIBUTES
    if unknown:
        raise OTelDriverError(f"disallowed attributes rejected: {', '.join(sorted(unknown))}")
    content = set(attrs) & CONTENT_ATTRIBUTES
    if content and not content_enabled:
        raise OTelDriverError(f"content capture disabled: {', '.join(sorted(content))}")
    # Content is never admitted by v1, even if collection is explicitly enabled;
    # a future redaction profile must define its canonical representation first.
    if content:
        raise OTelDriverError("content attributes have no approved Evidence Profile mapping")
    approved = {key: attrs[key] for key in sorted(set(attrs) & allowed)}
    for key, value in approved.items():
        if SECRET_RE.search(f"{key}={value}"):
            raise OTelDriverError(f"secret-bearing attribute rejected: {key}")
    return approved


@dataclass
class OTelSequenceGuard:
    """In-memory monotonic guard; persist one instance per authenticated source."""

    last_sequence: dict[str, int] = field(default_factory=dict)
    seen_batches: set[str] = field(default_factory=set)

    def accept(self, source_id: str, sequence: int, batch_id: str) -> None:
        if batch_id in self.seen_batches:
            raise OTelDriverError("OTLP batch replay rejected")
        expected = self.last_sequence.get(source_id, 0) + 1
        if sequence != expected:
            raise OTelDriverError(f"OTLP batch sequence rejected: expected {expected}, received {sequence}")
        self.last_sequence[source_id] = sequence
        self.seen_batches.add(batch_id)


def map_otlp_json(
    request: Mapping[str, Any],
    *,
    source_id: str,
    sequence: int,
    batch_id: str,
    guard: OTelSequenceGuard,
    now: datetime | None = None,
    max_clock_skew_seconds: int = 300,
    content_enabled: bool = False,
) -> tuple[dict[str, Any], ...]:
    """Map OTLP/HTTP JSON traces to unsigned Evidence Profile events.

    The caller must sign/hash-chain only the returned tuple. Any rejection occurs
    before output is returned, so partial batches cannot enter the evidence chain.
    """
    if not isinstance(request, Mapping) or set(request) != {"resourceSpans"}:
        raise OTelDriverError("OTLP request must contain only resourceSpans")
    groups = request["resourceSpans"]
    if not isinstance(groups, list) or not groups:
        raise OTelDriverError("resourceSpans must be a non-empty list")
    candidate: list[dict[str, Any]] = []
    clock = now or datetime.now(timezone.utc)
    for group in groups:
        if not isinstance(group, Mapping) or set(group) - {"resource", "scopeSpans", "schemaUrl"}:
            raise OTelDriverError("invalid resourceSpans schema")
        resource = group.get("resource", {})
        if not isinstance(resource, Mapping) or set(resource) - {"attributes", "droppedAttributesCount"}:
            raise OTelDriverError("invalid OTLP resource schema")
        resource_attrs = _approved(
            _attrs(resource.get("attributes"), path="resource.attributes"), RESOURCE_ALLOWLIST,
            content_enabled=content_enabled,
        )
        scopes = group.get("scopeSpans")
        if not isinstance(scopes, list) or not scopes:
            raise OTelDriverError("scopeSpans must be a non-empty list")
        for scope in scopes:
            if not isinstance(scope, Mapping) or set(scope) - {"scope", "spans", "schemaUrl"}:
                raise OTelDriverError("invalid scopeSpans schema")
            spans = scope.get("spans")
            if not isinstance(spans, list) or not spans:
                raise OTelDriverError("spans must be a non-empty list")
            for span in spans:
                allowed_span_keys = {"traceId", "spanId", "parentSpanId", "name", "kind", "startTimeUnixNano", "endTimeUnixNano", "attributes", "status", "flags", "traceState"}
                if not isinstance(span, Mapping) or set(span) - allowed_span_keys:
                    raise OTelDriverError("invalid OTLP span schema")
                trace_id, span_id = span.get("traceId"), span.get("spanId")
                if not isinstance(trace_id, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", trace_id):
                    raise OTelDriverError("traceId must be 16-byte hex")
                if not isinstance(span_id, str) or not re.fullmatch(r"[0-9a-fA-F]{16}", span_id):
                    raise OTelDriverError("spanId must be 8-byte hex")
                started = _iso_from_unix_nano(span.get("startTimeUnixNano"))
                ended = _iso_from_unix_nano(span.get("endTimeUnixNano"))
                started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                ended_dt = datetime.fromisoformat(ended.replace("Z", "+00:00"))
                if ended_dt < started_dt or abs((clock - ended_dt).total_seconds()) > max_clock_skew_seconds:
                    raise OTelDriverError("span clock skew/order rejected")
                span_attrs = _approved(
                    _attrs(span.get("attributes"), path="span.attributes"), SPAN_ALLOWLIST,
                    content_enabled=content_enabled,
                )
                event_id = hashlib.sha256(f"{source_id}:{batch_id}:{trace_id}:{span_id}".encode()).hexdigest()
                candidate.append({
                    "event_id": event_id,
                    "event_type": "llm_completion",
                    "logged_at": ended,
                    "schema_version": SCHEMA_VERSION,
                    "payload": {"resource": resource_attrs, "span": span_attrs},
                    "provenance": {"class": "vendor_otel", "component": "post_bind_ingest.otel_driver", "source_driver": SOURCE_DRIVER, "evidence_tier": EVIDENCE_TIER},
                    "redaction_applied": [],
                })
    if not candidate:
        raise OTelDriverError("OTLP request contains no spans")
    # Mutate replay state only after the entire batch passes validation.
    guard.accept(source_id, sequence, batch_id)
    return tuple(candidate)


def canonical_batch_sha256(events: tuple[dict[str, Any], ...]) -> str:
    return hashlib.sha256(json.dumps(events, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_signed_receiver_batch(
    events: tuple[dict[str, Any], ...],
    *,
    customer_id: str,
    agent_id: str,
    batch_id: str,
    issued_at: str,
    key_id: str,
    private_key: ed25519.Ed25519PrivateKey,
) -> dict[str, Any]:
    """Bind approved mapped events into the existing signed receiver contract."""
    if not events:
        raise OTelDriverError("cannot sign an empty OTLP event batch")
    claims = {
        "customer_id": customer_id,
        "agent": agent_id,
        "schema_version": "log-event-schema-v1.0",
        "batch_id": batch_id,
        "batch_sha256": f"sha256:{compute_batch_sha256(list(events))}",
        "event_count": len(events),
        "issued_at": issued_at,
    }
    header = {"alg": "EdDSA", "kid": key_id, "typ": "JWT"}
    header_part = _b64url(canonical_dumps(header))
    claims_part = _b64url(canonical_dumps(claims))
    signing_input = f"{header_part}.{claims_part}".encode("ascii")
    jwt = f"{header_part}.{claims_part}.{_b64url(private_key.sign(signing_input))}"
    return {**claims, "events": list(events), "jwt": jwt}


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
