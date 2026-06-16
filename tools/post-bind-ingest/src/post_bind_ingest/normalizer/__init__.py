"""Normalizer: raw vendor record -> typed Log Event Schema v1.0 event.

Determinism contract per Schema §Reproducibility and CTO brief AC#4:

  same input + same manifest_sha + same t_received_iso -> same output bytes

No clocks, no RNG, no live LLM. `t_received` is supplied by the caller (the
gateway records it once per manifest at receive time); the normalizer does not
read the wall clock. Event ids are derived from the manifest_sha + per-record
index, so replay is byte-stable across runs.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Iterable, Iterator

from .. import SCHEMA_VERSION
from . import event_families
from .event_families import (
    agent_invocation,
    error as error_family,
    escalation,
    llm_completion,
    pii_emission,
    security_event,
    tool_call,
    tool_source_reference,
)
from .invariants import diagnostic_to_security_event, enforce
from .redaction import (
    _DROPPED,
    apply_field_redaction,
    floor_scrub_secrets,
    merge_redaction_applied,
)
from .transcript_sha import canonical_json, sha256_of


_FAMILY_FIELDS: dict[str, tuple] = {
    agent_invocation.EVENT_TYPE: agent_invocation.FIELDS,
    llm_completion.EVENT_TYPE: llm_completion.FIELDS,
    tool_call.EVENT_TYPE: tool_call.FIELDS,
    tool_source_reference.EVENT_TYPE: tool_source_reference.FIELDS,
    escalation.EVENT_TYPE: escalation.FIELDS,
    error_family.EVENT_TYPE: error_family.FIELDS,
    security_event.EVENT_TYPE: security_event.FIELDS,
    pii_emission.EVENT_TYPE: pii_emission.FIELDS,
}


@dataclass(frozen=True)
class NormalizationContext:
    """Per-manifest context passed through normalization.

    `manifest_sha`, `customer_id`, `agent_id`, and `t_received` are taken from
    the verified manifest envelope (set by `ingest_gateway.manifest_receiver`).
    The normalizer never reads the wall clock.
    """
    manifest_sha: str
    customer_id: str
    agent_id: str
    source_kind: str
    t_received: str


def normalize_stream(
    raw_records: Iterable[dict],
    ctx: NormalizationContext,
) -> Iterator[dict]:
    """Yield typed `LogEventSchemaV1Event` dicts in input order.

    Each yielded event carries the full common envelope plus the per-family
    fields, all post-redaction. Invariant-violation diagnostics are emitted as
    `security_event` events inline; the offending event is still yielded
    (caller may drop it based on `partial=True` or the paired diagnostic).
    """
    typed = list(_to_typed_events(raw_records, ctx))
    for event, reports in enforce(typed):
        yield event
        for report in reports:
            yield diagnostic_to_security_event(
                report,
                customer_id=ctx.customer_id,
                agent_id=ctx.agent_id,
                manifest_sha=ctx.manifest_sha,
                source_kind=ctx.source_kind,
                t_received=ctx.t_received,
            )


def _to_typed_events(
    raw_records: Iterable[dict],
    ctx: NormalizationContext,
) -> Iterator[dict]:
    for index, record in enumerate(raw_records):
        kind = record["kind"]
        # raw_stdout adapter discriminator; other adapters mirror this shape.
        from .adapters.raw_stdout import RAW_STDOUT_KIND_TO_EVENT_TYPE

        event_type = RAW_STDOUT_KIND_TO_EVENT_TYPE.get(kind)
        if event_type is None:
            # Unknown discriminator -> defer to dead-letter at gateway layer.
            continue
        payload = record.get("payload") or {}
        yield _build_event(event_type, payload, ctx, index)


def _build_event(
    event_type: str,
    payload: dict,
    ctx: NormalizationContext,
    index: int,
) -> dict:
    fields = _FAMILY_FIELDS.get(event_type)
    if fields is None:
        raise ValueError(f"normalizer does not emit event_type {event_type!r}")

    # Deterministic event_id: derived from manifest_sha + index. The Schema
    # types this as `uuid`; we synthesize a UUIDv5 over the deterministic
    # seed so replay is byte-stable while still passing UUID validation.
    event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"backbond:{ctx.manifest_sha}:{index}"))

    redaction_applied: list[str] = []
    event: dict = {
        "schema_version": SCHEMA_VERSION,
        "event_type": event_type,
        "event_id": event_id,
        "customer_id": ctx.customer_id,
        "agent_id": ctx.agent_id,
        "session_id": payload.get("session_id", ""),
        "t_event": payload.get("t_event", ctx.t_received),
        "t_received": ctx.t_received,
        "source_kind": ctx.source_kind,
        "manifest_sha": ctx.manifest_sha,
        "redaction_applied": redaction_applied,
        "partial": bool(payload.get("partial", False)),
    }

    # Conditional turn_index per Schema §Common envelope.
    if event_type in {"llm_completion", "tool_call", "tool_source_reference", "escalation"}:
        ti = payload.get("turn_index")
        if isinstance(ti, int):
            event["turn_index"] = ti

    # Per-family fields with redaction + auto-compute *_sha fields from raw
    # preimages.
    for spec in fields:
        name = spec.name
        if name.endswith("_sha") and spec.provenance == "backbond_normalizer":
            preimage_key = _preimage_key_for(name)
            if preimage_key is not None and preimage_key in payload:
                event[name] = sha256_of(payload[preimage_key])
                continue
            if name in payload:  # caller pre-computed
                event[name] = payload[name]
                continue
            # Required *_sha missing -> mark partial.
            if spec.required == "yes":
                event["partial"] = True
            continue
        if name not in payload:
            if spec.required == "yes":
                event["partial"] = True
            continue
        value = payload[name]
        out_value, applied = apply_field_redaction(value, spec.redaction, name)
        if out_value is _DROPPED:
            merge_redaction_applied(redaction_applied, applied)
            continue
        event[name] = out_value
        merge_redaction_applied(redaction_applied, applied)

    return event


def _preimage_key_for(sha_field_name: str) -> str | None:
    """Map a *_sha field to its raw-preimage key on the payload.

    The mapping mirrors Schema v1.0 §Event family tables. Where the schema
    declares both `prompt_sha` (computed) and `prompt_preview` (optional, raw
    string), the preimage of the SHA is `prompt`, not `prompt_preview` — the
    preview is a truncated, redacted forensic view. The customer adapter
    supplies the raw preimage under the un-suffixed key; if the preimage is
    absent, the gateway's customer onboarding doc requires the customer to
    pre-compute and pass the SHA directly under the `*_sha` key.
    """
    if sha_field_name == "prompt_sha":
        return "prompt"
    if sha_field_name == "completion_sha":
        return "completion"
    if sha_field_name == "args_sha":
        return "args"
    if sha_field_name == "return_sha":
        return "return"
    if sha_field_name == "content_sha":
        return "content"
    if sha_field_name == "source_id_sha":
        return "source_id"
    if sha_field_name == "message_sha":
        return "message"
    if sha_field_name == "evidence_sha":
        return "evidence"
    if sha_field_name == "transcript_sha":
        return "transcript"
    if sha_field_name == "agent_invocation_args_sha":
        return "agent_invocation_args"
    return None


__all__ = [
    "NormalizationContext",
    "normalize_stream",
    "canonical_json",
    "sha256_of",
    "floor_scrub_secrets",
    "merge_redaction_applied",
]
