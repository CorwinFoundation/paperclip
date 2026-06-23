from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import os
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlparse

from .invariants import utc_now_iso
from .normalizer import EMAIL_RE, REDACTED_TOKEN_RE, SECRET_KEY_RE

MONITOR_DATA_CONTRACT_VERSION = "monitor-data-contract-v0.1"
TRANSMIT_ENV = "POST_BIND_INGEST_TRANSMIT"
ENDPOINT_ENV = "POST_BIND_INGEST_EGRESS_ENDPOINT"
MAX_EXCERPT_CHARS = 160
ALLOWED_EVENT_TYPES = frozenset(
    {
        "agent_invocation",
        "tool_call",
        "tool_source_reference",
        "llm_completion",
        "escalation",
        "error",
        "security_event",
        "pii_emission",
        "claim_candidate_event",
    }
)


class EgressGateError(ValueError):
    """Raised when a monitor artifact violates the outbound data contract."""


class EgressDisabled(EgressGateError):
    """Raised when transmit is requested without explicit opt-in."""


@dataclass(frozen=True)
class EgressConfig:
    transmit_enabled: bool = False
    endpoint: str | None = None
    allowed_hosts: tuple[str, ...] = ()
    max_excerpt_chars: int = MAX_EXCERPT_CHARS

    @classmethod
    def from_env(cls) -> "EgressConfig":
        endpoint = os.environ.get(ENDPOINT_ENV)
        allowed_hosts = (urlparse(endpoint).hostname or "",) if endpoint else ()
        return cls(
            transmit_enabled=os.environ.get(TRANSMIT_ENV, "").strip().lower() in {"1", "true", "yes", "on"},
            endpoint=endpoint,
            allowed_hosts=tuple(host for host in allowed_hosts if host),
        )


@dataclass(frozen=True)
class EgressResult:
    transmitted: bool
    endpoint: str | None
    bytes_prepared: int


def build_monitor_artifact(
    events: Iterable[Mapping[str, Any]],
    *,
    manifest_ref: str,
    generated_at: str | None = None,
    max_excerpt_chars: int = MAX_EXCERPT_CHARS,
) -> dict[str, Any]:
    """Build the only outbound monitor artifact shape for Phase 2.5.

    The artifact intentionally carries aggregates, a manifest pointer, and
    short excerpts that are already redacted in the normalized event stream.
    """

    _validate_manifest_ref(manifest_ref)
    events_tuple = tuple(events)
    event_types = Counter(str(event.get("event_type", "")) for event in events_tuple)
    redaction_count = sum(len(event.get("redactions") or ()) for event in events_tuple)
    artifact = {
        "schema_version": MONITOR_DATA_CONTRACT_VERSION,
        "generated_by": "post_bind_ingest.egress_gate",
        "generated_at": generated_at or utc_now_iso(),
        "manifest_refs": [manifest_ref],
        "aggregates": {
            "event_count": len(events_tuple),
            "event_type_counts": dict(sorted(event_types.items())),
            "redaction_count": redaction_count,
        },
        "excerpts": _build_redacted_excerpts(events_tuple, max_excerpt_chars=max_excerpt_chars),
    }
    validate_monitor_artifact(artifact, max_excerpt_chars=max_excerpt_chars)
    return artifact


def transmit_monitor_artifact(
    artifact: Mapping[str, Any],
    *,
    config: EgressConfig | None = None,
    transport: Callable[[str, bytes], None] | None = None,
) -> EgressResult:
    config = config or EgressConfig.from_env()
    validate_monitor_artifact(artifact, max_excerpt_chars=config.max_excerpt_chars)
    payload = json.dumps(artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    if not config.transmit_enabled:
        return EgressResult(transmitted=False, endpoint=None, bytes_prepared=len(payload))
    if not config.endpoint:
        raise EgressGateError("egress endpoint is required when transmit is enabled")
    _validate_endpoint(config.endpoint, config.allowed_hosts)
    if transport is None:
        raise EgressGateError("egress transport is not configured")
    transport(config.endpoint, payload)
    return EgressResult(transmitted=True, endpoint=config.endpoint, bytes_prepared=len(payload))


def validate_monitor_artifact(artifact: Mapping[str, Any], *, max_excerpt_chars: int = MAX_EXCERPT_CHARS) -> None:
    if not isinstance(artifact, Mapping):
        raise EgressGateError("monitor artifact must be an object")
    if artifact.get("schema_version") != MONITOR_DATA_CONTRACT_VERSION:
        raise EgressGateError("monitor artifact schema_version mismatch")

    manifest_refs = artifact.get("manifest_refs")
    if not isinstance(manifest_refs, list) or not manifest_refs:
        raise EgressGateError("monitor artifact requires manifest_refs")
    for ref in manifest_refs:
        if not isinstance(ref, str):
            raise EgressGateError("manifest_refs must contain strings")
        _validate_manifest_ref(ref)

    aggregates = artifact.get("aggregates")
    if not isinstance(aggregates, Mapping):
        raise EgressGateError("monitor artifact requires aggregates")
    _validate_aggregates(aggregates)

    excerpts = artifact.get("excerpts")
    if not isinstance(excerpts, list):
        raise EgressGateError("monitor artifact excerpts must be a list")
    for idx, excerpt in enumerate(excerpts):
        _validate_excerpt(excerpt, idx=idx, max_excerpt_chars=max_excerpt_chars)

    for path, value in _walk_strings(artifact):
        if path.startswith("excerpts[") and path.endswith(".text"):
            continue
        if EMAIL_RE.search(value) or SECRET_KEY_RE.search(value):
            raise EgressGateError(f"raw sensitive value cannot egress at {path}")


def _build_redacted_excerpts(
    events: Iterable[Mapping[str, Any]],
    *,
    max_excerpt_chars: int,
) -> list[dict[str, Any]]:
    excerpts: list[dict[str, Any]] = []
    for event in events:
        event_type = str(event.get("event_type", ""))
        event_id = str(event.get("event_id", ""))
        redacted_paths = {str(item.get("path")) for item in (event.get("redactions") or ()) if isinstance(item, Mapping)}
        for path, value in _walk_strings(event.get("payload", {})):
            if path not in redacted_paths:
                continue
            excerpt_text = value[:max_excerpt_chars]
            if len(value) > max_excerpt_chars:
                excerpt_text = excerpt_text.rstrip() + "..."
            excerpts.append(
                {
                    "event_id": event_id,
                    "event_type": event_type,
                    "path": path,
                    "text": excerpt_text,
                    "length": len(excerpt_text),
                    "redaction_conformance": "validated",
                }
            )
    return excerpts


def _validate_aggregates(aggregates: Mapping[str, Any]) -> None:
    event_count = aggregates.get("event_count")
    if not isinstance(event_count, int) or event_count < 0:
        raise EgressGateError("aggregates.event_count must be a non-negative integer")
    event_type_counts = aggregates.get("event_type_counts")
    if not isinstance(event_type_counts, Mapping):
        raise EgressGateError("aggregates.event_type_counts must be an object")
    for event_type, count in event_type_counts.items():
        if event_type not in ALLOWED_EVENT_TYPES:
            raise EgressGateError(f"unsupported aggregate event_type: {event_type!r}")
        if not isinstance(count, int) or count < 0:
            raise EgressGateError("event type counts must be non-negative integers")
    redaction_count = aggregates.get("redaction_count")
    if not isinstance(redaction_count, int) or redaction_count < 0:
        raise EgressGateError("aggregates.redaction_count must be a non-negative integer")


def _validate_excerpt(excerpt: Any, *, idx: int, max_excerpt_chars: int) -> None:
    if not isinstance(excerpt, Mapping):
        raise EgressGateError(f"excerpts[{idx}] must be an object")
    for field in ("event_id", "event_type", "path", "text", "redaction_conformance"):
        if not isinstance(excerpt.get(field), str) or not excerpt[field]:
            raise EgressGateError(f"excerpts[{idx}].{field} must be a non-empty string")
    if excerpt["event_type"] not in ALLOWED_EVENT_TYPES:
        raise EgressGateError(f"excerpts[{idx}].event_type unsupported")
    if excerpt["redaction_conformance"] != "validated":
        raise EgressGateError(f"excerpts[{idx}] must be redaction-conformance gated")
    text = excerpt["text"]
    if len(text) > max_excerpt_chars + 3:
        raise EgressGateError(f"excerpts[{idx}].text exceeds length cap")
    if not REDACTED_TOKEN_RE.search(text):
        raise EgressGateError(f"excerpts[{idx}].text must be a redacted excerpt")
    if EMAIL_RE.search(text) or SECRET_KEY_RE.search(text):
        raise EgressGateError(f"raw sensitive value cannot egress at excerpts[{idx}].text")


def _validate_manifest_ref(value: str) -> None:
    if not value.startswith("manifest://"):
        raise EgressGateError("manifest refs must use manifest://")
    if len(value) <= len("manifest://"):
        raise EgressGateError("manifest refs must include an identifier")


def _validate_endpoint(endpoint: str, allowed_hosts: tuple[str, ...]) -> None:
    parsed = urlparse(endpoint)
    if parsed.scheme != "https":
        raise EgressGateError("egress endpoint must use https")
    if not parsed.hostname:
        raise EgressGateError("egress endpoint host is required")
    if parsed.hostname not in allowed_hosts:
        raise EgressGateError("egress endpoint host is not allowlisted")


def _walk_strings(value: Any, path: str = ""):
    if isinstance(value, Mapping):
        for key, child in value.items():
            next_path = str(key) if not path else f"{path}.{key}"
            yield from _walk_strings(child, next_path)
        return
    if isinstance(value, list):
        for idx, child in enumerate(value):
            next_path = f"{path}[{idx}]"
            yield from _walk_strings(child, next_path)
        return
    if isinstance(value, str) and value:
        yield path, value
